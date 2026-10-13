"""Per-panel mentor scheduling from durable logical actions and outcomes."""


def mentor_decisions(parent, rounds, trigger):
    state = parent.state_json
    position = state["pairs"][-1]["position"]
    config = parent.source_snapshot["config"]["mentor"]
    policy = config["intervention_policy"]
    decisions = {}
    for panel in parent.panel_configs:
        identifier = panel["panel_id"]
        previous = [
            action
            for action in state["mentor_actions"]
            if action["panel_decisions"].get(identifier) == "admitted"
        ]
        automatic = [
            action["position"]
            for action in previous
            if action["trigger"] == "auto"
        ]
        decision = "admitted"
        if trigger == "auto" and position < policy["start_turn"]:
            decision = "before_start"
        elif (
            trigger == "auto"
            and automatic
            and position - max(automatic) < policy["min_interval_turns"]
        ):
            decision = "interval"
        elif config["mode"] == "auto":
            interventions = 0
            for action in previous:
                if action["position"] < max(
                    1, position - policy["window_turns"] + 1
                ):
                    continue
                child = next(
                    (
                        child
                        for child in rounds
                        if child["comparison_run_id"] == action["child_id"]
                    ),
                    None,
                )
                outcome = (
                    next(
                        (
                            panel
                            for panel in child["panels"]
                            if panel["panel_id"] == identifier
                        ),
                        None,
                    )
                    if child
                    else None
                )
                if outcome and any(
                    attempt["status"] == "running"
                    or (
                        attempt["status"] == "completed"
                        and (attempt.get("result") or {}).get(
                            "should_intervene"
                        )
                        is True
                    )
                    for attempt in [outcome, *outcome["attempts"]]
                ):
                    interventions += 1
            if interventions >= policy["max_interventions"]:
                decision = "cap"
        decisions[identifier] = decision
    return decisions
