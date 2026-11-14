import json
import uuid
from copy import deepcopy

from .errors import ApiNotFound
from .sugoroku_stages import BONUS, MAIN
from .sync import Result, diff_state


GOAL, BATTLE, FORCE_BATTLE = 1, 3, 4
FORWARD, BACK = 5, 6
DICE_PLUS, DICE_MINUS = 8, 9
CHEST, PASS_CHEST, RANDOM, CHECKPOINT = 10, 11, 12, 13
READY, ARRIVED, BRANCHING = "READY", "WAIT_ARRIVAL", "WAIT_BRANCH"
BATTLE_OFFERED, BATTLE_FORCED = "WAIT_ARRIVAL_OR_BATTLE", "WAIT_BATTLE"
OUT_OF_DICE, DEFEATED = "ENDED_WITH_THROW_UP", "ENDED_WITH_DEFEAT"
STALE_STAGE = "DIFF_STAGE_FILE_VER"
COMPLETE, FAILURE, SKIP = "COMPLETE", "FAILURE", "SKIP"
BOSS_BEATEN = 4
DICE = "dice"
FIXED_DICE = {f"fix_dice_{face}": face for face in range(1, 7)}
BATCH_LIMIT = 78
INBOX, DIRECT = "inbox", "direct"
SOURCE = "sugoroku.reward"


class Sugoroku:
    def __init__(self, masters, config, resources, mailbox, stages, rng):
        self.masters = masters
        self.config = config
        self.resources = resources
        self.mailbox = mailbox
        self.stages = stages
        self.rng = rng

    def _settings(self) -> dict:
        return self.config.read()["sugoroku"]

    @staticmethod
    def _session(state: dict, txid, missing=ValueError) -> dict:
        session = state["sugoroku"]["session"]
        if session is None or session["txid"] != txid:
            raise missing("txid")
        return session

    def _row(self, session: dict) -> dict:
        return self.masters.lookup("sugoroku_data", "stage_id", session["stage_id"])

    def _board(self, session: dict):
        return self.stages.stage(self._row(session)).boards[session["map_no"]]

    @staticmethod
    def _stats(session: dict) -> dict:
        return session["maps"][str(session["map_no"])]

    def _batch(self, session: dict) -> int:
        multiplied = self._settings()["multiply_by_cost_batch"]
        return session["cost_batch"] if multiplied else 1

    @staticmethod
    def _turn(number: int, cell: int, step: int, roll: int, phase: str) -> dict:
        return {
            "turn": number,
            "phase": phase,
            "start_step": step,
            "dice_roll": roll,
            "effect_items": [],
            "waypoints": [{"id": cell, "ex": None}],
            "rewards_in_turn": [],
            "inv_items_in_turn": [],
            "remain": 0,
        }

    def _close(self, session: dict) -> None:
        turn = session["turn"]
        phase = READY if session["num_of_dice"] > 0 else OUT_OF_DICE
        step = self._stats(session)["steps"]
        session["turn"] = self._turn(
            turn["turn"] + 1, session["cell"], step, turn["dice_roll"], phase
        )

    def _flags(self, state: dict, session: dict) -> int:
        beaten = session["stage_id"] in state["sugoroku"]["cleared"]
        unconditional = not self._settings()["boss_skip_requires_clear"]
        skippable = session["map_no"] == MAIN and (beaten or unconditional)
        return BOSS_BEATEN if skippable else 0

    def _context(self, state: dict, session: dict) -> dict:
        turn = {
            key: deepcopy(value)
            for key, value in session["turn"].items()
            if key != "remain"
        }
        return {
            "game_lv": session["game_lv"],
            "cost_batch": session["cost_batch"],
            "map_ctx": {
                "map_no": session["map_no"],
                **self._stats(session),
                "flags": self._flags(state, session),
            },
            # The client waits for the reply before it moves when no branch is predicted.
            "turn_ctx": {**turn, "branches": []},
            "num_of_dice": session["num_of_dice"],
            "used_units": list(session["used_units"]),
            # A "dice" row here would be added to the dice count a second time.
            "item_inventory": [
                {"name": name, "quantity": held}
                for name, held in session["items"].items()
                if held > 0
            ],
            "reserved_rewards": deepcopy(session["reserved"]),
        }

    @staticmethod
    def _mark(session: dict, cell: int) -> dict:
        turn = session["turn"]
        step = turn["start_step"] + len(turn["waypoints"]) - 1
        return {"mno": session["map_no"], "cid": cell, "step": step}

    def _reserve(self, session: dict, cell: int, rewards: list[dict]) -> None:
        if not rewards:
            return
        entry = {**self._mark(session, cell), "rewards": rewards}
        session["turn"]["rewards_in_turn"].append(entry)
        # A resumed client rebuilds its running total from the reserved list alone.
        session["reserved"].append(deepcopy(entry))

    @staticmethod
    def _open(row: dict, lap: int) -> bool:
        start, laps = row["loop_start"], row["loop_count"]
        return start <= lap and (not laps or lap < start + laps)

    def _prizes(self, lotteries: list[int], lap: int, batch: int) -> list[dict]:
        totals: dict[str, int] = {}
        for lottery in lotteries:
            rows = [
                row
                for row in self.masters.table("sugoroku_reward_data")
                if row["lottery_id"] == lottery
            ]
            drawn = [row for row in rows if self._open(row, lap)]
            reached = lap
            if not drawn and self._settings()["lap_overflow"] == "last":
                bounded = [row for row in rows if row["loop_count"]]
                reached = min(
                    lap,
                    max(
                        (row["loop_start"] + row["loop_count"] - 1 for row in bounded),
                        default=lap,
                    ),
                )
                drawn = [row for row in rows if self._open(row, reached)]
            prizes = [row for row in drawn if not row["odds"]]
            weighted = [row for row in drawn if row["odds"]]
            if weighted:
                shares = [row["odds"] for row in weighted]
                prizes.append(self.rng.choices(weighted, shares)[0])
            for row in prizes:
                amount = row["item_num"] + row["loop_add"] * (
                    reached - row["loop_start"]
                )
                if row["max_num"]:
                    amount = min(amount, row["max_num"])
                order = self.resources.reward_order(
                    {
                        "m_walletCategory": row["item_category"],
                        "m_walletName": row["item_name"],
                        "m_count": amount * batch,
                    }
                )
                if order is not None:
                    totals[order["rid"]] = totals.get(order["rid"], 0) + order["val"]
        return [{"rid": rid, "val": amount} for rid, amount in totals.items()]

    def _reward(self, board, session: dict, cell: int, lotteries: list[int]) -> None:
        # The lap was already counted when the piece entered a loop point.
        lap = self._stats(session)["laps"] - board.loops(cell)
        prizes = self._prizes(lotteries, lap, self._batch(session))
        self._reserve(session, cell, prizes)

    def _pass(self, board, session: dict, cell: int) -> None:
        ability, values = board.ability(cell), board.cell(cell)["values"]
        if ability == PASS_CHEST:
            self._reward(board, session, cell, values[:1])
        elif ability == CHECKPOINT:
            self._reward(board, session, cell, values[1:2])

    def _walk(
        self, board, session: dict, steps: int, backward=False, choice=None
    ) -> int:
        turn, stats = session["turn"], self._stats(session)
        cell, moved = session["cell"], False
        while steps > 0:
            exits = board.entries(cell) if backward else board.exits(cell)
            if not exits or (moved and board.ability(cell) == FORCE_BATTLE):
                break
            if len(exits) == 1:
                following = exits[0]
            elif backward:
                following = min(exits)
            elif choice:
                following, choice = choice, None
            else:
                session["cell"] = cell
                return steps
            if moved:
                self._pass(board, session, cell)
            cell, steps, moved = following, steps - 1, True
            turn["waypoints"].append({"id": cell, "ex": None})
            stats["steps"] += 1
            stats["laps"] += int(board.loops(cell))
        session["cell"] = cell
        return 0

    def _pick(self, board, cell: int) -> int:
        candidates = board.cell(cell)["values"]
        weights = self._settings()["random_cell_weights"]
        shares = [
            weights.get(str(board.cell(candidate)["category"]), 1)
            for candidate in candidates
        ]
        return self.rng.choices(candidates, shares)[0]

    def _settle(self, board, session: dict, left: int, chain: int = 0) -> None:
        turn = session["turn"]
        turn["remain"] = left
        if left:
            turn["phase"] = BRANCHING
            return
        landed = effect = session["cell"]
        if board.ability(landed) == RANDOM:
            effect = self._pick(board, landed)
            # Without the drawn cell the client would draw one of its own.
            turn["waypoints"][-1]["ex"] = json.dumps({"ref_id": effect})
        ability, values = board.ability(effect), board.cell(effect)["values"]
        if ability in (FORWARD, BACK) and chain < self._settings()["chain_limit"]:
            left = self._walk(board, session, values[0], backward=ability == BACK)
            self._settle(board, session, left, chain + 1)
            return
        if ability in (DICE_PLUS, DICE_MINUS):
            change = values[0] if ability == DICE_PLUS else -values[0]
            session["num_of_dice"] = max(0, session["num_of_dice"] + change)
            # Lets a resumed client undo the change before it replays this landing.
            turn["inv_items_in_turn"].append(
                {
                    **self._mark(session, landed),
                    "items": [{"name": DICE, "quantity": change}],
                }
            )
        elif ability in (CHEST, PASS_CHEST):
            self._reward(board, session, landed, values[:1])
        elif ability == CHECKPOINT:
            self._reward(board, session, landed, values[:2])
        turn["phase"] = {
            GOAL: BATTLE_FORCED,
            FORCE_BATTLE: BATTLE_FORCED,
            BATTLE: BATTLE_OFFERED,
        }.get(ability, ARRIVED)

    @staticmethod
    def _opening(session: dict, context: dict) -> dict:
        return {
            "txid": session["txid"],
            "context": context,
            "saved_deck": deepcopy(session["deck"]),
            "piece_chara_id": session["piece_chara_id"],
        }

    def _resume(self, state: dict, session: dict, payload: dict) -> Result:
        if payload["stage_id"] != session["stage_id"]:
            raise ValueError("Another sugoroku game is still open")
        context = self._context(state, session)
        if payload["stage_file_ver"] != session["stage_file_ver"]:
            context["turn_ctx"]["phase"] = STALE_STAGE
        return Result(self._opening(session, context))

    def start(self, state: dict, payload: dict, now: int) -> Result:
        record = state["sugoroku"]
        if record["session"] is not None:
            # Resuming and retrying a start both arrive here; neither pays again.
            return self._resume(state, record["session"], payload)
        stage_id, version = payload["stage_id"], payload["stage_file_ver"]
        level, batch = payload["game_lv"], payload["cost_batch"]
        piece = payload["piece_chara_id"]
        if any(type(value) is not int for value in (stage_id, level, batch, piece)):
            raise ValueError("Sugoroku start values must be integers")
        row = self.masters.lookup("sugoroku_data", "stage_id", stage_id)
        stage = self.stages.stage(row)
        settings = self._settings()
        term = row["start_date"], row["end_date"]
        closed = settings["enforce_master_dates"] and not self.masters.in_term(
            *term, now
        )
        if (
            closed
            or version != stage.version
            or level < 1
            or not 1 <= batch <= self.masters.property("Int", BATCH_LIMIT)
            or row["need_stage_id"] not in (0, *record["cleared"])
        ):
            raise ValueError("The sugoroku stage cannot be entered")
        before = deepcopy(state)
        if settings["charge_tickets"]:
            ticket = f"wallet.{row['consumption_category']}.{row['consumption_name']}"
            cost = {"rid": ticket, "val": row["consumption_num"] * batch}
            self.resources.spend(state, [cost], now)
        session = {
            "txid": f"sugoroku-{uuid.uuid4()}",
            "stage_id": stage_id,
            "stage_file_ver": version,
            "game_lv": level,
            "cost_batch": batch,
            "piece_chara_id": piece,
            "map_no": MAIN,
            "cell": 1,
            "maps": {str(MAIN): self._fresh_stats()},
            "num_of_dice": row["start_dice_count"],
            "items": {
                item["m_walletName"]: item["m_count"] for item in row["init_items"]
            },
            "used_units": [],
            "reserved": [],
            "deck": None,
            "turn": self._turn(1, 1, 0, 0, READY),
        }
        record["session"] = session
        opening = self._opening(session, self._context(state, session))
        return Result(opening, diff_state(before, state))

    @staticmethod
    def _fresh_stats() -> dict:
        return {"laps": 0, "steps": 0, "times_of_rolled": 0, "times_of_battle_win": 0}

    def query(self, state: dict) -> dict:
        record = state["sugoroku"]
        session = record["session"]
        return {
            "stage_id": session["stage_id"] if session else 0,
            "stage_list": list(record["cleared"]),
        }

    def _roll(self, session: dict, item) -> int:
        if session["turn"]["phase"] in (ARRIVED, BATTLE_OFFERED):
            # The client never waits for its turn/end, so the roll may overtake it.
            self._close(session)
        turn, items = session["turn"], session["items"]
        if turn["phase"] != READY or session["num_of_dice"] <= 0:
            raise ValueError("No dice can be rolled now")
        if item is None:
            face = self.rng.randint(1, 6)
        elif isinstance(item, str) and item in FIXED_DICE and items.get(item, 0) > 0:
            items[item] -= 1
            face = FIXED_DICE[item]
        else:
            raise ValueError("The dice item is not held")
        session["num_of_dice"] -= 1
        self._stats(session)["times_of_rolled"] += 1
        turn.update(dice_roll=face, effect_items=[])
        return face

    def advance(self, state: dict, payload: dict) -> dict:
        session = self._session(state, payload["txid"])
        branch, item = payload["select_branche"], payload["selected_item_for_roll"]
        if type(branch) is not int:
            raise ValueError("select_branche must be an integer")
        board = self._board(session)
        if branch:
            turn = session["turn"]
            exits = board.exits(session["cell"])
            if turn["phase"] != BRANCHING or branch not in exits:
                raise ValueError("There is no such branch to take")
            left = self._walk(board, session, turn["remain"], choice=branch)
        else:
            left = self._walk(board, session, self._roll(session, item))
        self._settle(board, session, left)
        return {"context": self._context(state, session)}

    def end_turn(self, state: dict, payload: dict) -> None:
        session = self._session(state, payload["txid"], ApiNotFound)
        if session["turn"]["phase"] in (ARRIVED, BATTLE_OFFERED):
            self._close(session)

    def _quest(self, board, session: dict) -> dict | None:
        values = board.cell(session["cell"])["values"][: session["game_lv"]]
        quest_id = next((value for value in reversed(values) if value), 0)
        try:
            return self.masters.lookup("sugoroku_quest_data", "id", quest_id)
        except KeyError:
            return None

    @staticmethod
    def _fighters(state: dict, session: dict, instances, rules, strict: bool) -> list:
        if not isinstance(instances, list) or any(
            type(instance) is not int for instance in instances
        ):
            raise ValueError("used_unit_instances must be a list of integers")
        owned = {unit["instance_id"] for unit in state["units"]}
        named = [instance for instance in instances if instance]
        fresh = [
            instance
            for instance in dict.fromkeys(named)
            if instance in owned and instance not in session["used_units"]
        ]
        limit = rules["deck_max"] if rules else len(named)
        # A skipped boss reports the saved deck, which may hold units already spent.
        if strict and (len(fresh) != len(named) or len(named) > limit):
            raise ValueError("The deck cannot fight this sugoroku battle")
        return fresh

    def _spoils(self, board, session: dict, rules: dict | None) -> None:
        cell, batch = session["cell"], self._batch(session)
        category = str(board.cell(cell)["category"])
        orders = self._settings()["battle_cell_rewards"].get(category, [])
        self.resources.check(orders)
        rewards = [
            {"rid": order["rid"], "val": order["val"] * batch} for order in orders
        ]
        self._reserve(session, cell, rewards)
        bonus = rules["dice_add"] if rules else 0
        if bonus:
            session["num_of_dice"] += bonus
            session["turn"]["inv_items_in_turn"].append(
                {
                    **self._mark(session, cell),
                    "items": [{"name": DICE, "quantity": bonus}],
                }
            )

    def _enter_bonus(self, session: dict) -> None:
        turn = session["turn"]
        session.update(map_no=BONUS, cell=1)
        session["maps"][str(BONUS)] = self._fresh_stats()
        session["num_of_dice"] += self._row(session)["bonus_dice_add"]
        session["turn"] = self._turn(turn["turn"] + 1, 1, 0, turn["dice_roll"], READY)

    def finish_battle(self, state: dict, payload: dict) -> dict:
        session = self._session(state, payload["txid"])
        board, turn = self._board(session), session["turn"]
        result, statement = payload["result"], payload["statement"]
        rating = statement.get("rating") if isinstance(statement, dict) else None
        forced = turn["phase"] == BATTLE_FORCED
        goal = session["map_no"] == MAIN and board.ability(session["cell"]) == GOAL
        if (
            turn["phase"] not in (BATTLE_FORCED, BATTLE_OFFERED)
            or result not in (COMPLETE, FAILURE, SKIP)
            or type(rating) is not int
            or (result == COMPLETE and rating <= 0)
            or (result == SKIP and not (goal and self._flags(state, session)))
        ):
            raise ValueError("The sugoroku battle cannot be settled")
        if result == FAILURE:
            if forced:
                turn["phase"] = DEFEATED
            return {
                "context": self._context(state, session),
                "next_stage_context": None,
            }
        rules = self._quest(board, session)
        session["used_units"] += self._fighters(
            state, session, payload["used_unit_instances"], rules, result == COMPLETE
        )
        self._stats(session)["times_of_battle_win"] += 1
        self._spoils(board, session, rules)
        context = self._context(state, session)
        # Closing the turn here keeps a resumed client from opening the battle again.
        if goal:
            self._enter_bonus(session)
        else:
            self._close(session)
        context["turn_ctx"]["phase"] = READY if goal else session["turn"]["phase"]
        following = self._context(state, session) if goal else None
        return {"context": context, "next_stage_context": following}

    def save_deck(self, state: dict, payload: dict) -> None:
        session = self._session(state, payload["txid"], ApiNotFound)
        deck = payload["deck"]
        slots = deck.get("slots") if isinstance(deck, dict) else None
        extra = deck.get("exdata") if isinstance(deck, dict) else None
        if (
            not isinstance(slots, list)
            or any(not isinstance(slot, str) for slot in slots)
            or not isinstance(extra, str | None)
        ):
            raise ValueError("The sugoroku deck is malformed")
        session["deck"] = {"slots": list(slots), "exdata": extra}

    def select_item(self, state: dict, payload: dict) -> None:
        session = self._session(state, payload["txid"], ApiNotFound)
        name, turn = payload["item_name"], session["turn"]
        if turn["phase"] != READY:
            return
        # The client sends the selected name once more to take the selection back.
        if turn["effect_items"][-1:] == [name]:
            turn["effect_items"] = []
        elif isinstance(name, str) and session["items"].get(name, 0) > 0:
            turn["effect_items"] = [name]
        else:
            raise ValueError("The dice item is not held")

    def _deliver(self, state: dict, session: dict, orders: list, now: int) -> list:
        source = f"{SOURCE}:{session['stage_id']}"
        delivery = self._settings()["delivery"]
        if delivery == DIRECT:
            changes = self.resources.grant(
                state, orders, now, policy="mail", source=source
            )
            return changes.deliveries
        if delivery != INBOX:
            raise ValueError(f"Unknown sugoroku delivery: {delivery}")
        self.resources.check(orders)
        title = self._row(session)["box_message"]
        for order in orders:
            self.mailbox.post(state, dict(order), source, now, title)
        return [
            {**order, "inc": 0, "inbox": order["val"], "over": 0} for order in orders
        ]

    def finish(self, state: dict, payload: dict, now: int) -> Result:
        record = state["sugoroku"]
        last = record["last_result"]
        if last is not None and last["txid"] == payload["txid"]:
            # A retried request must not pay the same game out twice.
            return Result(deepcopy(last["data"]))
        session = self._session(state, payload["txid"])
        totals: dict[str, int] = {}
        for entry in session["reserved"]:
            for order in entry["rewards"]:
                totals[order["rid"]] = totals.get(order["rid"], 0) + order["val"]
        orders = [{"rid": rid, "val": amount} for rid, amount in totals.items()]
        before = deepcopy(state)
        data = {
            # The client reads the last summary to learn whether the bonus map was reached.
            "map_summaries": [
                {"map_no": int(number), **stats, "flags": 0}
                for number, stats in session["maps"].items()
            ],
            "rewards": self._deliver(state, session, orders, now),
        }
        if (
            str(BONUS) in session["maps"]
            and session["stage_id"] not in record["cleared"]
        ):
            record["cleared"].append(session["stage_id"])
        record.update(
            session=None,
            last_result={"txid": session["txid"], "data": deepcopy(data)},
        )
        return Result(data, diff_state(before, state))
