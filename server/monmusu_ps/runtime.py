import json
import random
import secrets
import string
import time
from typing import Callable

from .config import RuntimeConfig, Settings
from .core.armory import Armory
from .core.attendance import Attendance
from .core.comeback import Comeback
from .core.credentials import Credentials
from .core.data_links import DataLinks
from .core.event_growth import EventGrowth
from .core.battle import BattleEngine
from .core.battle_logs import BattleLogs, Shelf
from .core.bingo import Bingo
from .core.calendar import Calendar
from .core.farming import Ranch
from .core.gacha import GachaEngine
from .core.gacha_box import BoxGacha
from .core.gacha_catalog import GachaCatalog
from .core.inbox import Inbox
from .core.initial import create_player
from .core.journal import Journal
from .core.limits import Limits
from .core.loginbonus import LoginBonus
from .core.lottery import Lottery
from .core.mail import Mailbox
from .core.masterdata import MasterData
from .core.membership import Membership
from .core.mileage import Mileage
from .core.migrations import MIGRATIONS
from .core.missions import Missions
from .core.news import News
from .core.passes import BonusPasses
from .core.payments import GACHA, SHOP, Payments
from .core.periods import Periods
from .core.platform import LocalPlatform
from .core.registry import FeatureRegistry
from .core.resources import Resources
from .core.serial_codes import SerialCodes
from .core.progression import Progression
from .core.quest_gates import QuestGates
from .core.reward_tracks import RewardTracks
from .core.client_layout import ClientLayout
from .core.layout import GUARDED, LOCKED
from .core.overrides import Overrides
from .core.localization import Localization
from .core.patch import ClientPatch
from .core.schedule import Schedule
from .core.sealed_characters import SealedCharacters
from .core.secret_quests import SecretQuests
from .core.sessions import Sessions
from .core.sync import Outbox
from .core.shop import Shop
from .core.social import Social, SocialGraph
from .core.store import PlayerStore
from .core.subdue_raids import SubdueRaids
from .core.sugoroku import Sugoroku
from .core.sugoroku_stages import Stages
from .core.story import Stories
from .core.subskill_prizes import SubskillPrizes
from .core.synthesis import Synthesis
from .core.teams import ClearTeams
from .core.tower import Tower
from .core.training_menus import TrainingMenus
from .core.training import Training
from .core.tutorial import Tutorial
from .core.unit_boosts import UnitBoosts
from .core.votes import Votes


class Runtime:
    def __init__(
        self, settings: Settings, clock: Callable[[], float] = time.time, rng=None
    ):
        self.settings = settings
        self.clock = clock
        self.rng = random.SystemRandom() if rng is None else rng
        self.config = RuntimeConfig(settings.root / "server/data/settings.json")
        self.masters = MasterData(settings.root / "masterdata")
        self.client_layout = ClientLayout(settings.root / "protocol/master_layout.json")
        self.overrides = Overrides(
            self.masters,
            RuntimeConfig(settings.root / "server/data/overrides.json"),
            LOCKED,
            self.client_layout,
            GUARDED,
        )
        self.schedule = Schedule(
            self.masters,
            RuntimeConfig(settings.root / "server/data/schedule.json"),
            self.config,
        )
        self.masters.overlays = [self.overrides, self.schedule]
        self.localization = Localization(self.masters, self.config, settings.root)
        self.patch = ClientPatch(
            self.masters,
            self.overrides,
            self.schedule,
            self.client_layout,
            self.localization,
        )
        self.calendar = Calendar(self.masters)
        self.mailbox = Mailbox(self.config, self.calendar)
        self.journal = Journal(self.config)
        self.attendance = Attendance(self.calendar, self.journal)
        self.progression = Progression(self.masters, self.config, self.journal)
        self.resources = Resources(
            self.masters, self.config, self.mailbox, self.progression
        )
        self.inbox = Inbox(
            self.config, self.mailbox, self.resources, self.localization.text
        )
        self.membership = Membership(
            self.config, self.calendar, self.resources, self.mailbox, self.journal
        )
        self.news = News(self.masters, self.config)
        self.bingo = Bingo(self.masters, self.config, self.resources, self.rng)
        self.mileage = Mileage(self.masters, self.config, self.resources)
        self.boosts = boosts = UnitBoosts(self.masters, self.config, self.progression)
        self.event_growth = EventGrowth(
            self.masters, self.config, self.resources, boosts
        )
        self.sealed_characters = SealedCharacters(
            self.masters, self.config, self.resources, boosts, self.journal
        )
        self.armory = Armory(self.masters, self.resources)
        self.training = Training(
            self.masters,
            self.resources,
            self.progression,
            self.armory,
            self.journal,
            self.rng,
        )
        self.synthesis = Synthesis(self.masters, self.resources)
        self.subskill_prizes = SubskillPrizes(self.masters, self.config, self.rng)
        self.tutorial = Tutorial(self.masters, self.journal)
        self.store = PlayerStore(settings.saves, self._create_player, MIGRATIONS)
        self.subdue_raids = SubdueRaids(
            self.masters, self.config, self.resources, self.mailbox, self.store
        )
        self.stories = Stories(
            self.masters, self.resources, self.journal, self.subdue_raids
        )
        self.periods = Periods(self.masters, self.config, self.calendar, self.journal)
        self.missions = Missions(
            self.masters,
            self.calendar,
            self.resources,
            self.mailbox,
            self.stories,
            self.journal,
        )
        self.data_links = DataLinks(
            settings.saves / "data_links.json", self.config, self.store
        )
        self.votes = Votes(
            self.masters,
            self.config,
            self.schedule,
            self.calendar,
            self.resources,
            self.store,
        )
        self.sugoroku = Sugoroku(
            self.masters,
            self.config,
            self.resources,
            self.mailbox,
            Stages(settings.root / "server/data/sugoroku", self.masters),
            self.rng,
        )
        graph = SocialGraph(settings.saves / "social.json")
        self.social = Social(
            self.masters,
            self.config,
            RuntimeConfig(settings.root / "server/data/social_roster.json"),
            self.calendar,
            self.resources,
            self.store,
            self.journal,
            graph,
        )
        tracks = RewardTracks(self.masters, self.resources)
        self.secret_quests = SecretQuests(
            self.masters,
            self.config,
            self.resources,
            self.mailbox,
            tracks,
            self.store,
        )
        self.training_menus = TrainingMenus(self.masters)
        self.tower = Tower(
            self.masters,
            self.config,
            self.resources,
            self.mailbox,
            tracks,
            self.journal,
        )
        self.battles = BattleEngine(
            self.masters,
            self.config,
            self.resources,
            self.progression,
            self.tutorial,
            self.social,
            self.journal,
            self.rng,
            gates=QuestGates(self.masters, self.secret_quests),
            prizes=self.subskill_prizes,
            secrets=self.secret_quests,
            training=self.training_menus,
            tower=self.tower,
        )
        self.limits = Limits(self.calendar)
        self.boxes = RuntimeConfig(settings.root / "server/data/box_gacha.json")
        self.gacha = GachaEngine(
            GachaCatalog(self.masters),
            Lottery(self.masters, self.config, self.rng),
            BoxGacha(self.boxes, self.masters, self.resources, self.rng),
            self.config,
            self.resources,
            self.limits,
            self.periods,
            self.mailbox,
            self.tutorial,
            self.journal,
            self.rng,
        )
        self.shop = Shop(
            self.masters,
            self.config,
            self.resources,
            self.limits,
            self.periods,
            self.mailbox,
            self.journal,
        )
        self.payments = Payments(
            self.masters,
            {GACHA: self.gacha.admit_prepaid, SHOP: self.shop.admit_prepaid},
        )
        self.bonus_passes = BonusPasses(
            self.resources.passes, self.resources, self.mailbox
        )
        self.ranch = Ranch(self.masters, self.resources)
        self.serial_codes = SerialCodes(
            self.masters, self.config, self.resources, self.mailbox
        )
        self.comeback = Comeback(
            self.masters,
            self.config,
            self.gacha.catalog,
            self.periods,
            self.limits,
            self.journal,
        )
        self.login_bonus = LoginBonus(
            self.masters,
            self.calendar,
            self.mailbox,
            self.resources,
            self.tutorial,
            self.comeback,
            self.social,
        )
        self.battle_logs = BattleLogs(
            self.masters,
            self.config,
            Shelf(settings.saves / "battle_logs"),
            self.store,
            graph,
            self.journal,
            settings.public_url,
        )
        self.clear_teams = ClearTeams(self.masters, self.store, self.battle_logs)
        self.sessions = Sessions()
        self.outbox = Outbox()
        self.credentials = Credentials(settings.saves / "credentials.json")
        self.platform = LocalPlatform(settings.saves / "platform.json", self.config)
        contracts = json.loads(
            (settings.root / "protocol/contracts.json").read_text(encoding="utf-8")
        )
        self.contracts = {
            row["endpoint"]: row
            for row in contracts["routes"]
            if row["family"] == "ntd"
        }
        schema = json.loads(
            (settings.root / "protocol/api_schema.json").read_text(encoding="utf-8")
        )
        self.models = schema["models"]
        self.registry = FeatureRegistry(
            settings.root / "server/monmusu_ps/features", self.contracts
        )

    def _create_player(self, profile: str) -> dict:
        codes = {
            document["state"]["player"]["player_code"]
            for document in self.store.documents()
        }
        alphabet = string.ascii_uppercase + string.digits
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        while code in codes:
            code = "".join(secrets.choice(alphabet) for _ in range(8))
        return create_player(
            profile, code, self.masters, self.config.read(), int(self.clock())
        )

    async def order(self, viewer_id: str, sku: str, quantity) -> str:
        profile = self.data_links.resolve(self.platform.save_profile(viewer_id))
        if not self.store.exists(profile):
            raise KeyError(f"No save for platform account {viewer_id}")
        async with self.store.transaction(profile) as state:
            return self.payments.issue(state, sku, quantity, int(self.clock()))

    def profile_of(self, player_code: str) -> str:
        for document in self.store.documents():
            if document["state"]["player"]["player_code"] == player_code:
                return document["profile"]
        raise KeyError(f"Unknown player: {player_code}")

    def has_response_data(self, endpoint: str) -> bool:
        model = self.contracts[endpoint]["response_models"]["200"]
        return any(
            field["name"] == "data" for field in self.models[model]["effective_fields"]
        )
