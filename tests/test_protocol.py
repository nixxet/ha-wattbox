"""Pure parser/encoder tests.

Fixtures are verbatim captures from live probes against:
- 10.10.10.150 (WB-250-IPW-2, firmware 2.9.0.2)
- 10.10.10.156 (WB-800-IPVM-12, firmware 2.10.0.0)

If the device dialect changes, these fixtures should be updated from
fresh captures rather than guessed.
"""

from __future__ import annotations

import pytest

from wattbox_local import (
    BatteryHealth,
    PowerStatus,
    UPSStatus,
    WattboxCommandUnsupported,
    WattboxProtocolError,
)
from wattbox_local.protocol import (
    CMD_MODEL,
    CMD_POWER_STATUS,
    CMD_UPS_STATUS,
    OUTLET_OFF,
    OUTLET_ON,
    OUTLET_RESET,
    encode_auto_reboot,
    encode_outlet_set,
    expect_value,
    is_async_push,
    parse_auto_reboot,
    parse_int,
    parse_lockout_remaining_s,
    parse_outlet_names,
    parse_outlet_status,
    parse_power_status,
    parse_ups_connection,
    parse_ups_status,
    split_response,
)

# --- live capture fixtures -----------------------------------------------

WB250_FIRMWARE = "?Firmware=2.9.0.2"
WB250_MODEL = "?Model=WB-250-IPW-2"
WB250_HOSTNAME = "?Hostname=WattBoxGarage"
WB250_OUTLET_COUNT = "?OutletCount=2"
WB250_OUTLET_STATUS = "?OutletStatus=1,1"
WB250_OUTLET_NAME = "?OutletName={Outlet 1},{Outlet 2}"
WB250_POWER_STATUS_ERROR = "#Error"

WB800_MODEL = "?Model=WB-800-IPVM-12"
WB800_OUTLET_COUNT = "?OutletCount=12"
WB800_OUTLET_STATUS = "?OutletStatus=1,1,1,1,1,1,1,1,1,1,1,1"
WB800_OUTLET_NAME = (
    "?OutletName={Dish Hopper},{EA3},{Denon Rcv},{Cheap LED Strip},"
    "{Outlet 5},{Nvidia Shield},{WTC UPS},{Dream Machine},"
    "{Vivint Camera},{Hikvision NVR},{PS5},{Unifi 24 Switch}"
)
WB800_POWER_STATUS = "?PowerStatus=0.27,81.88,123.69,0"
WB800_UPS_STATUS = "?UPSStatus=100,8,Good,False,160,True,False"
WB800_UPS_CONNECTION = "?UPSConnection=1"
WB800_AUTO_REBOOT_OFF = "?AutoReboot=0"


# --- expect_value --------------------------------------------------------


class TestExpectValue:
    def test_strips_known_prefix(self) -> None:
        assert expect_value(CMD_MODEL, WB250_MODEL) == "WB-250-IPW-2"

    def test_strips_known_prefix_with_trailing_whitespace(self) -> None:
        assert expect_value(CMD_MODEL, WB250_MODEL + "\r\n") == "WB-250-IPW-2"

    def test_error_response_raises_unsupported(self) -> None:
        with pytest.raises(WattboxCommandUnsupported) as exc:
            expect_value(CMD_POWER_STATUS, WB250_POWER_STATUS_ERROR)
        assert exc.value.command == CMD_POWER_STATUS

    def test_wrong_prefix_raises_protocol_error(self) -> None:
        with pytest.raises(WattboxProtocolError):
            expect_value(CMD_MODEL, "?Hostname=Nope")

    def test_no_equals_raises_protocol_error(self) -> None:
        with pytest.raises(WattboxProtocolError):
            expect_value(CMD_MODEL, "garbage")


# --- split_response ------------------------------------------------------


class TestSplitResponse:
    def test_typical_pair(self) -> None:
        assert split_response(WB250_MODEL) == ("?Model", "WB-250-IPW-2")

    def test_pair_with_whitespace(self) -> None:
        assert split_response("  ?Firmware=2.9.0.2  \r\n") == ("?Firmware", "2.9.0.2")

    def test_blank_line_is_none(self) -> None:
        assert split_response("") is None
        assert split_response("   \r\n") is None

    def test_banner_is_none(self) -> None:
        assert split_response("Please Login to Continue") is None
        assert split_response("Successfully Logged In!") is None

    def test_error_raises(self) -> None:
        with pytest.raises(WattboxCommandUnsupported):
            split_response("#Error")


# --- parse_outlet_status -------------------------------------------------


class TestParseOutletStatus:
    def test_wb250_all_on(self) -> None:
        value = expect_value("?OutletStatus", WB250_OUTLET_STATUS)
        assert parse_outlet_status(value) == [True, True]

    def test_wb800_all_on(self) -> None:
        value = expect_value("?OutletStatus", WB800_OUTLET_STATUS)
        assert parse_outlet_status(value) == [True] * 12

    def test_mixed(self) -> None:
        assert parse_outlet_status("1,0,1,1,0") == [True, False, True, True, False]

    def test_empty_value_is_empty_list(self) -> None:
        assert parse_outlet_status("") == []

    def test_bad_token_raises(self) -> None:
        with pytest.raises(WattboxProtocolError):
            parse_outlet_status("1,2,1")


# --- parse_outlet_names --------------------------------------------------


class TestParseOutletNames:
    def test_wb250_default_names(self) -> None:
        value = expect_value("?OutletName", WB250_OUTLET_NAME)
        assert parse_outlet_names(value) == ["Outlet 1", "Outlet 2"]

    def test_wb800_friendly_names(self) -> None:
        value = expect_value("?OutletName", WB800_OUTLET_NAME)
        assert parse_outlet_names(value) == [
            "Dish Hopper",
            "EA3",
            "Denon Rcv",
            "Cheap LED Strip",
            "Outlet 5",
            "Nvidia Shield",
            "WTC UPS",
            "Dream Machine",
            "Vivint Camera",
            "Hikvision NVR",
            "PS5",
            "Unifi 24 Switch",
        ]

    def test_name_containing_comma_survives(self) -> None:
        # braces are the real delimiter, commas inside names are fine
        assert parse_outlet_names("{Living Room, Lamp},{Office}") == [
            "Living Room, Lamp",
            "Office",
        ]

    def test_empty_value_is_empty_list(self) -> None:
        assert parse_outlet_names("") == []

    def test_unterminated_brace_raises(self) -> None:
        with pytest.raises(WattboxProtocolError):
            parse_outlet_names("{Foo},{Bar")

    def test_missing_open_brace_raises(self) -> None:
        with pytest.raises(WattboxProtocolError):
            parse_outlet_names("Foo,{Bar}")


# --- parse_power_status --------------------------------------------------


class TestParsePowerStatus:
    def test_wb800_live_capture(self) -> None:
        value = expect_value("?PowerStatus", WB800_POWER_STATUS)
        result = parse_power_status(value)
        assert result == PowerStatus(
            current_amps=0.27,
            power_watts=81.88,
            voltage_volts=123.69,
            safe_voltage=False,
        )

    def test_safe_voltage_true_when_one(self) -> None:
        assert parse_power_status("0.27,81.88,123.69,1").safe_voltage is True

    def test_wrong_field_count_raises(self) -> None:
        with pytest.raises(WattboxProtocolError):
            parse_power_status("0.27,81.88,123.69")

    def test_non_numeric_raises(self) -> None:
        with pytest.raises(WattboxProtocolError):
            parse_power_status("a,b,c,0")

    def test_wb250_error_raises_unsupported(self) -> None:
        with pytest.raises(WattboxCommandUnsupported):
            expect_value("?PowerStatus", "#Error")


# --- parse_ups_status ----------------------------------------------------


class TestParseUpsStatus:
    def test_wb800_live_capture(self) -> None:
        value = expect_value(CMD_UPS_STATUS, WB800_UPS_STATUS)
        result = parse_ups_status(value)
        assert result == UPSStatus(
            battery_charge_pct=100,
            battery_load_pct=8,
            battery_health=BatteryHealth.GOOD,
            power_lost=False,
            battery_runtime_min=160,
            alarm_enabled=True,
            alarm_muted=False,
        )

    def test_unknown_health_falls_back(self) -> None:
        result = parse_ups_status("50,12,Mediocre,True,30,False,True")
        assert result.battery_health == BatteryHealth.UNKNOWN
        assert result.power_lost is True
        assert result.alarm_enabled is False
        assert result.alarm_muted is True

    @pytest.mark.parametrize("bad", ["100,8,Good,False,160,True", "100,8,Good"])
    def test_wrong_field_count_raises(self, bad: str) -> None:
        with pytest.raises(WattboxProtocolError):
            parse_ups_status(bad)

    def test_non_numeric_charge_raises(self) -> None:
        with pytest.raises(WattboxProtocolError):
            parse_ups_status("oops,8,Good,False,160,True,False")

    def test_bad_bool_token_raises(self) -> None:
        with pytest.raises(WattboxProtocolError):
            parse_ups_status("100,8,Good,maybe,160,True,False")


# --- parse_ups_connection ------------------------------------------------


class TestParseUpsConnection:
    def test_connected(self) -> None:
        assert parse_ups_connection(expect_value("?UPSConnection", WB800_UPS_CONNECTION)) is True

    def test_not_connected(self) -> None:
        assert parse_ups_connection("0") is False

    def test_bad_value_raises(self) -> None:
        with pytest.raises(WattboxProtocolError):
            parse_ups_connection("2")


# --- parse_auto_reboot ---------------------------------------------------


class TestParseAutoReboot:
    def test_off(self) -> None:
        assert parse_auto_reboot(expect_value("?AutoReboot", WB800_AUTO_REBOOT_OFF)) is False

    def test_on(self) -> None:
        assert parse_auto_reboot("1") is True

    def test_bad_raises(self) -> None:
        with pytest.raises(WattboxProtocolError):
            parse_auto_reboot("maybe")


# --- parse_int -----------------------------------------------------------


class TestParseInt:
    def test_outlet_count_wb250(self) -> None:
        v = expect_value("?OutletCount", WB250_OUTLET_COUNT)
        assert parse_int(v, command="?OutletCount") == 2

    def test_outlet_count_wb800(self) -> None:
        v = expect_value("?OutletCount", WB800_OUTLET_COUNT)
        assert parse_int(v, command="?OutletCount") == 12

    def test_non_integer_raises(self) -> None:
        with pytest.raises(WattboxProtocolError):
            parse_int("abc", command="?OutletCount")


# --- encoders ------------------------------------------------------------


class TestEncodeOutletSet:
    @pytest.mark.parametrize(
        ("idx", "action", "expected"),
        [
            (1, OUTLET_ON, "!OutletSet=1,ON"),
            (4, OUTLET_OFF, "!OutletSet=4,OFF"),
            (12, OUTLET_RESET, "!OutletSet=12,RESET"),
        ],
    )
    def test_valid(self, idx: int, action: str, expected: str) -> None:
        assert encode_outlet_set(idx, action) == expected

    def test_zero_index_rejected(self) -> None:
        with pytest.raises(ValueError):
            encode_outlet_set(0, OUTLET_ON)

    def test_negative_index_rejected(self) -> None:
        with pytest.raises(ValueError):
            encode_outlet_set(-1, OUTLET_ON)

    def test_bad_action_rejected(self) -> None:
        with pytest.raises(ValueError):
            encode_outlet_set(1, "INVALID")

    def test_toggle_action_accepted(self) -> None:
        # TOGGLE is documented as a valid action in the vendor PDF.
        assert encode_outlet_set(7, "TOGGLE") == "!OutletSet=7,TOGGLE"

    def test_reset_with_delay(self) -> None:
        assert encode_outlet_set(3, "RESET", delay=10) == "!OutletSet=3,RESET,10"

    def test_reset_delay_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            encode_outlet_set(3, "RESET", delay=0)
        with pytest.raises(ValueError):
            encode_outlet_set(3, "RESET", delay=601)

    def test_delay_with_non_reset_rejected(self) -> None:
        with pytest.raises(ValueError):
            encode_outlet_set(3, "ON", delay=5)

    def test_reset_all_with_index_zero(self) -> None:
        # Per vendor PDF: index 0 + RESET means "reset all outlets".
        assert encode_outlet_set(0, "RESET") == "!OutletSet=0,RESET"

    def test_index_zero_with_non_reset_rejected(self) -> None:
        with pytest.raises(ValueError):
            encode_outlet_set(0, "ON")


class TestEncodeAutoReboot:
    def test_on(self) -> None:
        assert encode_auto_reboot(True) == "!AutoReboot=1"

    def test_off(self) -> None:
        assert encode_auto_reboot(False) == "!AutoReboot=0"


# --- new encoders (vendor PDF v2.4) -------------------------------------


class TestEncodeOutletNameSet:
    def test_basic(self) -> None:
        from wattbox_local.protocol import encode_outlet_name_set

        assert encode_outlet_name_set(5, "Server") == "!OutletNameSet=5,{Server}"

    def test_name_with_space_is_preserved_via_braces(self) -> None:
        from wattbox_local.protocol import encode_outlet_name_set

        assert encode_outlet_name_set(5, "Living Room") == "!OutletNameSet=5,{Living Room}"

    def test_rejects_bad_index(self) -> None:
        from wattbox_local.protocol import encode_outlet_name_set

        with pytest.raises(ValueError):
            encode_outlet_name_set(0, "x")

    def test_rejects_braces_and_newlines(self) -> None:
        from wattbox_local.protocol import encode_outlet_name_set

        for bad in ("{Foo", "Foo}", "with\nnewline", "with\rcarriage"):
            with pytest.raises(ValueError):
                encode_outlet_name_set(1, bad)

    def test_rejects_empty(self) -> None:
        from wattbox_local.protocol import encode_outlet_name_set

        with pytest.raises(ValueError):
            encode_outlet_name_set(1, "")


class TestEncodeOutletNameSetAll:
    def test_two_names(self) -> None:
        from wattbox_local.protocol import encode_outlet_name_set_all

        assert encode_outlet_name_set_all(["A", "B"]) == "!OutletNameSetAll={A},{B}"

    def test_names_with_spaces_ok(self) -> None:
        from wattbox_local.protocol import encode_outlet_name_set_all

        assert encode_outlet_name_set_all(["Dish Hopper", "Denon Rcv"]) == (
            "!OutletNameSetAll={Dish Hopper},{Denon Rcv}"
        )

    def test_empty_list_rejected(self) -> None:
        from wattbox_local.protocol import encode_outlet_name_set_all

        with pytest.raises(ValueError):
            encode_outlet_name_set_all([])

    def test_bad_chars_rejected(self) -> None:
        from wattbox_local.protocol import encode_outlet_name_set_all

        with pytest.raises(ValueError):
            encode_outlet_name_set_all(["ok", "{nope}"])


class TestEncodeOutletPowerOnDelaySet:
    def test_basic(self) -> None:
        from wattbox_local.protocol import encode_outlet_power_on_delay_set

        assert encode_outlet_power_on_delay_set(3, 10) == "!OutletPowerOnDelaySet=3,10"

    def test_out_of_range(self) -> None:
        from wattbox_local.protocol import encode_outlet_power_on_delay_set

        with pytest.raises(ValueError):
            encode_outlet_power_on_delay_set(1, 0)
        with pytest.raises(ValueError):
            encode_outlet_power_on_delay_set(1, 601)


class TestEncodeOutletModeSet:
    @pytest.mark.parametrize("mode", [0, 1, 2])
    def test_valid(self, mode: int) -> None:
        from wattbox_local.protocol import encode_outlet_mode_set

        assert encode_outlet_mode_set(2, mode) == f"!OutletModeSet=2,{mode}"

    def test_invalid_mode_rejected(self) -> None:
        from wattbox_local.protocol import encode_outlet_mode_set

        with pytest.raises(ValueError):
            encode_outlet_mode_set(2, 3)


class TestEncodeOutletRebootSet:
    def test_two_outlets(self) -> None:
        from wattbox_local.protocol import encode_outlet_reboot_set

        assert encode_outlet_reboot_set([0, 1]) == "!OutletRebootSet=0,1"

    def test_invalid_op_rejected(self) -> None:
        from wattbox_local.protocol import encode_outlet_reboot_set

        with pytest.raises(ValueError):
            encode_outlet_reboot_set([0, 2])

    def test_empty_rejected(self) -> None:
        from wattbox_local.protocol import encode_outlet_reboot_set

        with pytest.raises(ValueError):
            encode_outlet_reboot_set([])


class TestEncodeAutoRebootTimeoutSet:
    def test_basic(self) -> None:
        from wattbox_local.protocol import encode_auto_reboot_timeout_set

        assert encode_auto_reboot_timeout_set(30, 3, 5, 0) == "!AutoRebootTimeoutSet=30,3,5,0"

    def test_out_of_range(self) -> None:
        from wattbox_local.protocol import encode_auto_reboot_timeout_set

        with pytest.raises(ValueError):
            encode_auto_reboot_timeout_set(0, 1, 1, 0)  # timeout < 1
        with pytest.raises(ValueError):
            encode_auto_reboot_timeout_set(30, 11, 1, 0)  # count > 10
        with pytest.raises(ValueError):
            encode_auto_reboot_timeout_set(30, 3, 31, 0)  # ping_delay > 30
        with pytest.raises(ValueError):
            encode_auto_reboot_timeout_set(30, 3, 5, -1)
        with pytest.raises(ValueError):
            encode_auto_reboot_timeout_set(30, 3, 5, 11)


class TestEncodeHostAdd:
    def test_basic(self) -> None:
        from wattbox_local.protocol import encode_host_add

        assert encode_host_add("Modem", "8.8.8.8", [1, 2]) == "!HostAdd=Modem,8.8.8.8,{1,2}"

    def test_empty_outlets_rejected(self) -> None:
        from wattbox_local.protocol import encode_host_add

        with pytest.raises(ValueError):
            encode_host_add("x", "8.8.8.8", [])

    def test_bad_chars_rejected(self) -> None:
        from wattbox_local.protocol import encode_host_add

        with pytest.raises(ValueError):
            encode_host_add("a,b", "8.8.8.8", [1])
        with pytest.raises(ValueError):
            encode_host_add("ok", "8.8.{8}.8", [1])


class TestEncodeScheduleAdd:
    def test_recurring_mwf(self) -> None:
        from wattbox_local.protocol import encode_schedule_add

        result = encode_schedule_add(
            "Nightly",
            [1, 2, 3],
            1,  # ON
            days=(False, True, False, True, False, True, False),  # M W F
            time="03:30",
        )
        assert result == "!ScheduleAdd={Nightly},{1,2,3},{1},{1},{0,1,0,1,0,1,0},{03:30}"

    def test_once_dated(self) -> None:
        from wattbox_local.protocol import encode_schedule_add

        result = encode_schedule_add(
            "Boxing day",
            [1],
            2,  # RESET
            date="2026/12/26",
            time="04:00",
        )
        assert result == "!ScheduleAdd={Boxing day},{1},{2},{0},{2026/12/26},{04:00}"

    def test_both_days_and_date_rejected(self) -> None:
        from wattbox_local.protocol import encode_schedule_add

        with pytest.raises(ValueError):
            encode_schedule_add(
                "X",
                [1],
                0,
                days=(True, False, False, False, False, False, False),
                date="2026/01/01",
                time="00:00",
            )

    def test_neither_days_nor_date_rejected(self) -> None:
        from wattbox_local.protocol import encode_schedule_add

        with pytest.raises(ValueError):
            encode_schedule_add("X", [1], 0, time="00:00")

    def test_bad_time_rejected(self) -> None:
        from wattbox_local.protocol import encode_schedule_add

        for bad in ("25:00", "9:30", "12:60", "noon"):
            with pytest.raises(ValueError):
                encode_schedule_add("X", [1], 0, days=(True,) * 7, time=bad)

    def test_bad_date_rejected(self) -> None:
        from wattbox_local.protocol import encode_schedule_add

        for bad in ("2026-01-01", "2026/13/01", "2026/01/32", "26/01/01"):
            with pytest.raises(ValueError):
                encode_schedule_add("X", [1], 0, date=bad, time="00:00")

    def test_invalid_action_rejected(self) -> None:
        from wattbox_local.protocol import encode_schedule_add

        with pytest.raises(ValueError):
            encode_schedule_add("X", [1], 99, days=(True,) * 7, time="00:00")


class TestParsePowerOnDelays:
    def test_wb800_live(self) -> None:
        from wattbox_local.protocol import parse_power_on_delays

        # Captured live from 10.10.10.156.
        assert parse_power_on_delays("11,4,10,31,5,12,2,7,8,9,30,6") == [
            11,
            4,
            10,
            31,
            5,
            12,
            2,
            7,
            8,
            9,
            30,
            6,
        ]

    def test_empty_is_empty_list(self) -> None:
        from wattbox_local.protocol import parse_power_on_delays

        assert parse_power_on_delays("") == []

    def test_bad_numeric_raises(self) -> None:
        from wattbox_local.protocol import parse_power_on_delays

        with pytest.raises(WattboxProtocolError):
            parse_power_on_delays("11,X,10")


# --- async push handling ------------------------------------------------


class TestAsyncPushPrefix:
    """The device emits ~Cmd=value as async state-change notifications.

    `expect_value` accepts either ?Cmd= or ~Cmd= as a valid prefix because
    the payload shape is identical and either may be the response we land
    on for a given command.
    """

    def test_tilde_prefix_accepted_for_query(self) -> None:
        # Verified live: !OutletSet=N,ON can be acked with a ~OutletStatus= push.
        assert expect_value("?OutletStatus", "~OutletStatus=1,0") == "1,0"

    def test_tilde_prefix_for_power_status(self) -> None:
        assert expect_value("?PowerStatus", "~PowerStatus=0.27,81.88,123.69,0") == (
            "0.27,81.88,123.69,0"
        )

    def test_wrong_command_name_still_rejected(self) -> None:
        with pytest.raises(WattboxProtocolError):
            expect_value("?OutletStatus", "~PowerStatus=0,0,0,0")

    def test_is_async_push_recognises_tilde(self) -> None:
        assert is_async_push("~OutletStatus=1,0")
        assert is_async_push("  ~OutletStatus=1,0  \r\n")

    def test_is_async_push_rejects_query_and_set(self) -> None:
        assert not is_async_push("?OutletStatus=1,0")
        assert not is_async_push("!OutletSet=1,ON")
        assert not is_async_push("OK")
        assert not is_async_push("#Error")
        assert not is_async_push("")


# --- lockout banner parsing --------------------------------------------


class TestParseLockoutRemainingS:
    """The device emits 'API is locked for X minutes and Y seconds.'"""

    def test_minutes_and_seconds(self) -> None:
        assert parse_lockout_remaining_s("API is locked for 4 minutes and 39 seconds.") == 279

    def test_only_minutes(self) -> None:
        assert parse_lockout_remaining_s("API is locked for 5 minutes.") == 300

    def test_only_seconds(self) -> None:
        assert parse_lockout_remaining_s("API is locked for 45 seconds.") == 45

    def test_singular_units(self) -> None:
        assert parse_lockout_remaining_s("API is locked for 1 minute and 1 second.") == 61

    def test_no_match_returns_none(self) -> None:
        assert parse_lockout_remaining_s("Please Login to Continue") is None

    def test_zero_duration_returns_none(self) -> None:
        # "0 minutes and 0 seconds" is effectively "not locked" — don't claim a lockout.
        assert parse_lockout_remaining_s("API is locked for 0 minutes and 0 seconds.") is None
