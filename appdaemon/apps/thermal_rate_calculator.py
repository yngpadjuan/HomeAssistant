"""
Calculates the observed thermal rate of a home from recorder history.

Only free-running hours are used: HVAC must be idle AND a configurable
cooldown period must have elapsed since the last active cycle, so that
post-HVAC temperature stabilisation does not contaminate the sample set.
"""

import appdaemon.plugins.hass.hassapi as hass
from datetime import datetime, timedelta
import statistics


class ThermalRateCalculator(hass.Hass):
    """Publishes sensor.observed_thermal_rate from historical free-running samples.

    The rate is the Newton's Law of Cooling coefficient, derived per sample as:
        rate = (T_indoor_next - T_indoor_now) / (T_outdoor - T_indoor_now)

    A positive rate means indoor drifts toward outdoor, as expected.

    Required apps.yaml keys:
        indoor_entity:      climate entity to read current_temperature attribute
        outdoor_entity:     sensor entity for outdoor temperature
        hvac_action_entity: sensor entity tracking hvac_action state
        output_sensor:      entity ID to publish results to
        lookback_days:      history window in days (default 7)
        cooldown_hours:     hours to skip after HVAC goes idle (default 1)
        update_time:        daily recalculation time, HH:MM:SS (default 03:00:00)
    """

    def initialize(self) -> None:
        self.indoor_entity: str = self.args["indoor_entity"]
        self.outdoor_entity: str = self.args["outdoor_entity"]
        self.hvac_action_entity: str = self.args["hvac_action_entity"]
        self.output_sensor: str = self.args["output_sensor"]
        self.lookback_days: int = int(self.args.get("lookback_days", 7))
        self.cooldown_hours: int = int(self.args.get("cooldown_hours", 1))
        update_time: str = self.args.get("update_time", "03:00:00")

        self.run_daily(self.calculate, update_time)
        self.run_in(self.calculate, 30)

    def calculate(self, kwargs: dict) -> None:
        """Fetch history, filter free-running samples, and publish the median rate."""
        end = datetime.now()
        start = end - timedelta(days=self.lookback_days)

        indoor_history = self.get_history(
            entity_id=self.indoor_entity, start_time=start, end_time=end
        )
        outdoor_history = self.get_history(
            entity_id=self.outdoor_entity, start_time=start, end_time=end
        )
        hvac_history = self.get_history(
            entity_id=self.hvac_action_entity, start_time=start, end_time=end
        )

        if not indoor_history or not outdoor_history or not hvac_history:
            self.log("Missing history data, skipping calculation.", level="WARNING")
            return

        indoor_series = self._to_float_series(
            indoor_history[0], attr="current_temperature"
        )
        outdoor_series = self._to_float_series(outdoor_history[0])
        hvac_states = hvac_history[0]

        last_active_end = self._last_active_end_times(hvac_states)

        rates: list[float] = []
        for i in range(len(indoor_series) - 1):
            ts_now, t_indoor_now = indoor_series[i]
            _, t_indoor_next = indoor_series[i + 1]

            if not self._is_free_running(ts_now, hvac_states, last_active_end):
                continue

            t_outdoor = self._value_at(outdoor_series, ts_now)
            if t_outdoor is None:
                continue

            gap = t_outdoor - t_indoor_now
            # Ignore near-equilibrium samples — gap too small to produce a reliable ratio
            if abs(gap) < 0.5:
                continue

            rate = (t_indoor_next - t_indoor_now) / gap
            if 0.001 <= rate <= 0.95:
                rates.append(rate)

        if len(rates) < 10:
            self.log(
                f"Insufficient free-running samples ({len(rates)}), skipping update.",
                level="WARNING",
            )
            return

        median_rate = round(statistics.median(rates), 4)
        mean_rate = round(statistics.mean(rates), 4)
        stdev = round(statistics.stdev(rates), 4) if len(rates) > 1 else 0

        self.set_state(
            self.output_sensor,
            state=median_rate,
            attributes={
                "friendly_name": "Observed Thermal Rate",
                "unit_of_measurement": "rate/hr",
                "mean": mean_rate,
                "stdev": stdev,
                "sample_count": len(rates),
                "lookback_days": self.lookback_days,
                "cooldown_hours": self.cooldown_hours,
                "icon": "mdi:thermometer-lines",
            },
        )
        self.log(
            "Thermal rate updated: median=%s, mean=%s, stdev=%s, samples=%s",
            median_rate,
            mean_rate,
            stdev,
            len(rates),
        )

    def _to_float_series(
        self, states: list[dict], attr: str | None = None
    ) -> list[tuple[datetime, float]]:
        """Parse a history state list into (timestamp, float) pairs, dropping unparseable entries."""
        result: list[tuple[datetime, float]] = []
        for s in states:
            try:
                val = float(s["attributes"].get(attr)) if attr else float(s["state"])
                result.append((s["last_changed"], val))
            except (TypeError, ValueError):
                pass
        return result

    def _value_at(
        self, series: list[tuple[datetime, float]], dt: datetime
    ) -> float | None:
        """Return the most recent value at or before dt, or None if no such entry exists."""
        best = None
        for ts, val in series:
            if ts <= dt:
                best = val
            else:
                break
        return best

    def _last_active_end_times(self, hvac_states: list[dict]) -> list[datetime]:
        """Return timestamps when HVAC transitioned from active to idle/off.

        Notes:
            Used to compute the cooldown window after each active cycle ends.
        """
        transitions: list[datetime] = []
        prev_active = False
        for s in hvac_states:
            is_active = s["state"] not in ("idle", "off")
            if prev_active and not is_active:
                transitions.append(s["last_changed"])
            prev_active = is_active
        return transitions

    def _is_free_running(
        self,
        dt: datetime,
        hvac_states: list[dict],
        last_active_end: list[datetime],
    ) -> bool:
        """Return True if dt is outside an active HVAC cycle and past the cooldown window.

        Notes:
            Cooldown window is self.cooldown_hours after each active-to-idle transition.
            Samples within the cooldown period are discarded to avoid post-HVAC
            temperature stabilisation skewing the rate estimate.
        """
        # Check HVAC is currently idle at dt
        current_state = None
        for s in hvac_states:
            if s["last_changed"] <= dt:
                current_state = s["state"]
            else:
                break
        if current_state not in (None, "idle", "off"):
            return False

        # Check we are past the cooldown window from the most recent active cycle end
        cooldown = timedelta(hours=self.cooldown_hours)
        for end_ts in reversed(last_active_end):
            if end_ts <= dt:
                return (dt - end_ts) >= cooldown
        return True
