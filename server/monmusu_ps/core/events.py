SUBJUGATION = 8


def chapter_event(masters, chapter: int, kinds) -> dict | None:
    rows = [
        row for row in masters.table("event_list_data") if row["event_type"] in kinds
    ]
    # An event owns its own chapter before the chapters it merely contains.
    return next(
        (row for row in rows if row["chapter"] == chapter),
        next((row for row in rows if chapter in row["contain_chapters"]), None),
    )


def raised_by_event(masters, card_id: int, now: int) -> bool:
    rows = masters.table("event_character_growth")
    growth = next((row for row in rows if row["card_id"] == card_id), None)
    if growth is None:
        return False
    try:
        event = masters.lookup("event_list_data", "id", growth["event_id"])
    except KeyError:
        return False
    if event["event_type"] == SUBJUGATION:
        return True
    archived = any(
        story["event_id"] == event["id"] for story in masters.table("sub_story_data")
    )
    running = masters.in_term(event["open_time"], event["close_time"], now)
    return running and not archived
