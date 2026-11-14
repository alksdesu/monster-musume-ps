class RewardTracks:
    def __init__(self, masters, resources):
        self.masters = masters
        self.resources = resources

    def _nodes(self, table_id: int) -> list[dict]:
        rows = [
            row
            for row in self.masters.table("reward_track_table")
            if row["table_id"] == table_id
        ]
        return sorted(rows, key=lambda row: row["track_value"])

    def crossed(self, table_id: int, before: int, after: int) -> list[dict]:
        return [
            {
                "ordinal": ordinal,
                "rewards": [
                    order
                    for row in node["items"]["m_rewards"]
                    if (order := self.resources.reward_order(row)) is not None
                ],
            }
            for ordinal, node in enumerate(self._nodes(table_id), 1)
            if before < node["track_value"] <= after
        ]
