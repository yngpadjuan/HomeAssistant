import appdaemon.plugins.hass.hassapi as hass
from datetime import datetime, timedelta
import statistics


class ThermalRateCalculator(hass.Hass):
    """
    Calculates the observed thermal rate of a home by analysing the past week
    of recorder history. Only hours where the HVAC was idle are used so that
    active heating/cooling does not skew the result.

    The rate is derived from Newton's Law of Cooling applied in reverse:
        rate = (T_indoor_next - T_indoor_now) / (T_outdoor - T_indoor_now)

    A positive rate means indoor is drifting toward outdoor (expected).
    The resulting sensor can be fed directly into the predictive-temperature blueprint.

    Required apps.yaml config:
        thermal_rate_calculator:
          module: thermal_rate_calculator
          class: ThermalRateCalculator
          indoor_entity: climate.hallway
          outdoor_entity: sensor.hourly_forecast
          hvac_action_entity: sensor.hvac_action
          output_sensor: sensor.observed_thermal_rate
          lookback_days: 7
          update_time: "03:00:00"
    """

    def initialize(self):
        self.indoor_entity = self.args["indoor_entity"]
        self.outdoor_entity = self.args["outdoor_entity"]
        self.hvac_action_entity = self.args["hvac_action_entity"]
        self.output_sensor = self.args["output_sensor"]
        self.lookback_days = int(self.args.get("lookback_days", 7))
        update_time = self.args.get("update_time", "03:00:00")

        self.run_daily(self.calculate, update_time)
        self.run_in(self.calculate, 30)

    def calculate(self, kwargs):
        end = datetime.now()
        start = end - timedelta(days=self.lookback_days)

        indoor_history = self.get_history(
            entity_id=self.indoor_entity,
            start_time=start,
            end_time=end,
        )
        outdoor_history = self.get_history(
            entity_id=self.outdoor_entity,
            start_time=start,
            end_time=end,
        )
        hvac_history = self.get_history(
            entity_id=self.hvac_action_entity,
            start_time=start,
            end_time=end,
        )

        if not indoor_history or not outdoor_history or not hvac_history:
            self.log("Missing history data, skipping calculation.")
            return

        indoor_states = indoor_history[0]
        outdoor_states = outdoor_history[0]
        hvac_states = hvac_history[0]

        def to_float_series(states, attr=None):
            result = []
            for s in states:
                try:
                    val = (
                        float(s["attributes"].get(attr)) if attr else float(s["state"])
                    )
                    result.append((s["last_changed"], val))
                except (TypeError, ValueError):
                    pass
            return result

        indoor_series = to_float_series(indoor_states, attr="current_temperature")
        outdoor_series = to_float_series(outdoor_states)

        def value_at(series, dt):
            """Return the most recent value at or before dt."""
            best = None
            for ts, val in series:
                if ts <= dt:
                    best = val
                else:
                    break
            return best

        def hvac_idle_at(dt):
            """Return True if hvac_action was idle at dt."""
            best = None
            for s in hvac_states:
                if s["last_changed"] <= dt:
                    best = s["state"]
                else:
                    break
            return best in (None, "idle", "off")

        rates = []
        for i in range(len(indoor_series) - 1):
            ts_now, t_indoor_now = indoor_series[i]
            ts_next, t_indoor_next = indoor_series[i + 1]

            if not hvac_idle_at(ts_now):
                continue

            t_outdoor = value_at(outdoor_series, ts_now)
            if t_outdoor is None:
                continue

            gap = t_outdoor - t_indoor_now
            if abs(gap) < 0.5:
                continue

            rate = (t_indoor_next - t_indoor_now) / gap
            if 0.001 <= rate <= 0.95:
                rates.append(rate)

        if len(rates) < 10:
            self.log(f"Insufficient idle samples ({len(rates)}), skipping update.")
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
                "icon": "mdi:thermometer-lines",
            },
        )
        self.log(
            f"Thermal rate updated: median={median_rate}, mean={mean_rate}, "
            f"stdev={stdev}, samples={len(rates)}"
        )
