# python_scripts/set_state.py
entity_id = data.get('entity_id')
state = data.get('state')

if entity_id and state:
    entity_ids = [entity_id] if isinstance(entity_id, str) else entity_id
    for id in entity_ids:
        # 1. Fetch the existing entity object from HA
        current_state_obj = hass.states.get(id)
        if current_state_obj:
            # 2. Extract and copy existing attributes (maintains state_class, etc.)
            attributes = current_state_obj.attributes.copy()
            # Fallback to current state if no new state is supplied
            if state is None:
                state = current_state_obj.state
        else:
            # If entity doesn't exist yet, start fresh
            attributes = {}
            if state is None:
                state = "unknown"

        # 3. Handle any extra data arguments passed via the service call 
        # (e.g., if you also want to pass a specific attribute on the fly)
        for key, value in data.items():
            if key not in ["entity_id", "state"]:
                attributes[key] = value

        # 4. Overwrite the state safely with original attributes intact
        hass.states.set(id, state, attributes)

        #old overwrite everything
        #hass.states.set(id,state)
else:
    logger.error("You must provide both entity_id and state.")
