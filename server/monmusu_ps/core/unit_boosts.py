class UnitBoosts:
    def __init__(self, masters, config, progression):
        self.masters = masters
        self.config = config
        self.progression = progression

    def level(self, state: dict, unit: dict, level: int, now: int) -> bool:
        needed = self.progression.level_exp(unit, level) - unit["t_exp"]
        if needed <= 0:
            return False
        self.progression.add_unit_exp(state, [unit["instance_id"]], needed, now)
        return True

    def awaken(self, unit: dict, points: int) -> bool:
        cap = self.config.read()["resources"]["duplicate_unit"]["potential_cap"]
        gained = min(points, cap - unit["upa"]["all_pt"])
        if gained <= 0:
            return False
        unit["upa"]["all_pt"] += gained
        return True

    def promote(self, unit: dict, rank: int) -> bool:
        card = self.masters.asset("unitcard", unit["card_id"])
        try:
            self.masters.lookup("job_data", "id", card["classId"] + rank)
        except KeyError:
            return False
        if unit["rank"] >= rank:
            return False
        unit["rank"] = rank
        return True

    @staticmethod
    def exskill(unit: dict, level: int) -> bool:
        if unit["exskill_lv"] >= level:
            return False
        unit["exskill_lv"] = level
        return True
