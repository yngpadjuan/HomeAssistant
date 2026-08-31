# python_scripts/set_state.py
entity_id = data.get('entity_id')
state = data.get('state')

if entity_id and state:
    entity_ids = [entity_id] if isinstance(entity_id, str) else entity_id
    for id in entity_ids:
        hass.states.set(id, state)
else:
    logger.error("You must provide both entity_id and state.")
