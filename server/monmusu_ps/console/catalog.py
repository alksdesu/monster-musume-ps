from dataclasses import dataclass
import re


DATE = re.compile(r"^\d{4}/\d{2}/\d{2}( \d{2}:\d{2}(:\d{2})?)?$")
RESOURCE = re.compile(
    r"^(wallet|item|unit\.card|bom\.card|skill\.attachable|stamina|bonus_pass|loginbonus)\."
)
NUMBERS = (int, float)
OTHER = "other"
# A row is named by the first of these its table carries.
TITLES = (
    "title",
    "name",
    "chapter_name",
    "display_name",
    "wallet_name",
    "item_name",
    "tab_name",
    "period_name",
    "scenario_name",
    "message",
    "text",
    "header_title",
    "description",
)


@dataclass(frozen=True)
class Group:
    key: str
    title: str
    blurb: str


GROUPS = (
    Group("shop", "商店与付费", "商品、页签、价格、限购和付费商品。"),
    Group("gacha", "卡池", "卡池本体、奖品、附赠、步进、印章。"),
    Group("mission", "任务", "任务条件、奖励、分组和面板任务。"),
    Group("login", "签到与兑换", "签到表、追加签到和兑换码活动。"),
    Group("event", "活动", "活动列表、讨伐、宾果、双六、累计奖励、神殿之塔。"),
    Group("pass", "通行证与月票", "通行证档位、奖励轨和月票赛季。"),
    Group("quest", "关卡", "章节、开放条件、ヒミツ、训练菜单和引导文本。"),
    Group("story", "剧情与图鉴", "剧情解锁条件、图鉴、画廊、皮肤和影片。"),
    Group("growth", "养成消耗", "升级、觉醒、技能和专武的材料与金币消耗，玩家等级表。"),
    Group("item", "道具与货币", "道具、货币、券的名称、说明和持有上限。"),
    Group("notice", "公告与界面", "横幅、公告图、新闻、界面文本、帮助、提示。"),
    Group(OTHER, "其他", "没有归到上面各类的表。"),
)

TABLES = {
    "shop_data": ("shop", "商店商品"),
    "shop_tab": ("shop", "商店页签"),
    "shop_collabo_system": ("shop", "联动商店"),
    "shop_guide_message_data": ("shop", "商店引导文本"),
    "payment_data": ("shop", "付费商品"),
    "expired_item": ("shop", "限时道具"),
    "item_limit_table": ("shop", "持有上限与转换"),
    "gacha_data": ("gacha", "卡池"),
    "gacha_detail_data": ("gacha", "卡池说明"),
    "gacha_prize_data": ("gacha", "卡池奖品"),
    "gacha_extra_prize_data": ("gacha", "卡池附赠"),
    "gacha_rnd_extra_prize_data": ("gacha", "卡池随机附赠"),
    "gacha_step": ("gacha", "步进卡池"),
    "gacha_stamp_sheet_data": ("gacha", "卡池印章表"),
    "gacha_stamp_cell_data": ("gacha", "卡池印章格"),
    "mission_data": ("mission", "任务"),
    "mission_category_collective_data": ("mission", "任务分组"),
    "mission_panel_frame": ("mission", "面板任务"),
    "mission_panel_content": ("mission", "面板任务内容"),
    "login_bonus": ("login", "签到表"),
    "login_bonus_cell": ("login", "签到格"),
    "additional_login_bonus": ("login", "追加签到"),
    "serial_code_campaign_data": ("login", "兑换码活动"),
    "event_list_data": ("event", "活动"),
    "event_character_growth": ("event", "活动角色成长"),
    "event_sealed_chara_props": ("event", "封印角色"),
    "everyones_killevent_data": ("event", "みんなで討伐"),
    "everyones_killevent_episode": ("event", "みんなで討伐剧情"),
    "everyones_killevent_quest": ("event", "みんなで討伐关卡"),
    "everyones_killevent_reward": ("event", "みんなで討伐奖励"),
    "bingo_data": ("event", "宾果"),
    "bingo_sheet": ("event", "宾果卡"),
    "bingo_line_data": ("event", "宾果连线奖励"),
    "sugoroku_data": ("event", "双六"),
    "sugoroku_quest_data": ("event", "双六关卡"),
    "sugoroku_reward_data": ("event", "双六奖励"),
    "sugoroku_cell_info_data": ("event", "双六格子"),
    "reward_track_table": ("event", "累计奖励"),
    "ranch_reward_table": ("event", "牧场奖励"),
    "temple_tower_control": ("event", "神殿之塔"),
    "temple_tower_point_generation": ("event", "神殿之塔点数"),
    "temple_tower_score_calc": ("event", "神殿之塔计分"),
    "bonus_pass_data": ("pass", "通行证"),
    "bonus_pass_tier_data": ("pass", "通行证档位"),
    "bonus_pass_track_reward_data": ("pass", "通行证奖励"),
    "season_pass_data": ("pass", "月票赛季"),
    "quest_data": ("quest", "关卡"),
    "quest_chapter_data": ("quest", "关卡章节"),
    "chapter_data": ("quest", "章节"),
    "quest_release_condition": ("quest", "关卡开放条件"),
    "quest_secret_category_table": ("quest", "ヒミツ分类"),
    "quest_secret_group_table": ("quest", "ヒミツ分组"),
    "quest_guide_message_data": ("quest", "关卡引导文本"),
    "quest_synopsis_message_data": ("quest", "关卡简介"),
    "training_menu": ("quest", "训练菜单"),
    "training_rank": ("quest", "训练评级"),
    "sub_skill_prize_table": ("quest", "副技能掉落"),
    "story_data_main": ("story", "主线剧情"),
    "story_data_event": ("story", "活动剧情"),
    "story_data_group": ("story", "群像剧情"),
    "story_data_other": ("story", "其他剧情"),
    "story_data_unit": ("story", "角色剧情"),
    "main_story_data": ("story", "主线剧情章节"),
    "novel_story_data": ("story", "小说剧情"),
    "sub_story_data": ("story", "支线剧情"),
    "sub_story_tab_data": ("story", "支线剧情页签"),
    "interact_story_data": ("story", "互动剧情"),
    "picturebook_data": ("story", "图鉴"),
    "gallery_data": ("story", "画廊"),
    "unit_skin_data": ("story", "皮肤"),
    "movie_link": ("story", "影片"),
    "recipe_skill": ("growth", "技能强化配方"),
    "recipe_ability": ("growth", "能力强化配方"),
    "subskill_recipe_data": ("growth", "副技能合成配方"),
    "unique_weapon_reinforce_item_data": ("growth", "专武强化材料"),
    "unit_awake_item": ("growth", "觉醒材料"),
    "unit_upgrade_cost": ("growth", "觉醒费用"),
    "unit_cost_exp_table": ("growth", "出击费用经验表"),
    "unit_level_exp_table": ("growth", "角色等级经验表"),
    "unit_skill_exp_table": ("growth", "技能经验表"),
    "player_rank_exp_table": ("growth", "玩家等级经验表"),
    "player_rank_info_table": ("growth", "玩家等级"),
    "charm_table": ("growth", "护符"),
    "charm_ability_meta_data": ("growth", "护符能力"),
    "gear_rarity_info": ("growth", "装备稀有度信息"),
    "composition_template": ("growth", "编成模板"),
    "item_template": ("item", "道具"),
    "wallet_template": ("item", "货币与券"),
    "term_template": ("item", "期限模板"),
    "banner_data": ("notice", "横幅"),
    "announce_image_data": ("notice", "公告图"),
    "news_data": ("notice", "新闻"),
    "header_message_data": ("notice", "顶栏消息"),
    "message_data": ("notice", "界面文本"),
    "help_data": ("notice", "帮助"),
    "help_home": ("notice", "帮助首页"),
    "help_window_data": ("notice", "帮助窗口"),
    "faq_data": ("notice", "常见问题"),
    "tips_data": ("notice", "提示"),
    "tips_loading_image": ("notice", "加载图"),
    "tutorial_message_data": ("notice", "教程文本"),
    "detail_text_data": ("notice", "详情文本"),
}

FIELDS = {
    "id": "编号",
    "name": "名称",
    "title": "标题",
    "text": "说明",
    "message": "内容",
    "description": "描述",
    "display_name": "显示名",
    "wallet_name": "货币名",
    "item_name": "道具名",
    "tab_name": "页签名",
    "chapter_name": "章节名",
    "period_name": "期间名",
    "scenario_name": "剧本",
    "category": "分类",
    "type": "类型",
    "rarity": "稀有度",
    "rank": "等级",
    "chapter": "章节",
    "chapter_id": "章节编号",
    "quest_id": "关卡编号",
    "story_id": "剧情编号",
    "story_category": "剧情分类",
    "event_id": "活动编号",
    "shop_id": "商店编号",
    "gacha_id": "卡池编号",
    "pass_id": "通行证编号",
    "table_id": "表编号",
    "card_id": "角色卡编号",
    "unit_card_id": "角色卡编号",
    "chara_id": "角色编号",
    "resource_id": "资源编号",
    "next_id": "下一项",
    "sort_priority": "排序",
    "sort_order": "排序",
    "priority": "优先级",
    "view_priority": "显示优先级",
    "startDate": "开始",
    "endDate": "结束",
    "start_date": "开始",
    "end_date": "结束",
    "start_at": "开始",
    "end_at": "结束",
    "start_dt": "开始",
    "end_dt": "结束",
    "startAt": "开始",
    "endAt": "结束",
    "open_time": "开放",
    "close_time": "关闭",
    "close_dt": "关闭",
    "close_date": "关闭",
    "expire_at": "过期",
    "post_date": "发布",
    "event_date": "活动日期",
    "date": "日期",
    "st_date": "开始",
    "ed_date": "结束",
    "price": "价格",
    "price_category": "价格类别",
    "price_name": "价格货币",
    "cost_num": "消耗数量",
    "cost_resource": "消耗资源",
    "consumption_category": "消耗类别",
    "consumption_name": "消耗货币",
    "sale_price": "出售价",
    "salable": "可出售",
    "val_cap": "持有上限",
    "max_num": "上限",
    "count": "数量",
    "value": "数值",
    "rate": "概率",
    "step": "步数",
    "tier": "档位",
    "reward": "奖励",
    "rewards": "奖励",
    "rewardtable": "奖励表",
    "prizes": "奖品",
    "recipes": "配方",
    "r18": "成人内容",
    "always": "始终开放",
    "unlockLevel": "解锁等级",
    "is_normal_hide": "默认隐藏",
    "isMainChapter": "主线章节",
    "targetPlatform": "目标平台",
    "target_platform": "目标平台",
    "box_message": "弹窗文本",
    "my_page_btn": "首页按钮",
    "bg_resources": "背景资源",
    "button_resources": "按钮资源",
    "title_logo_resources": "标题图资源",
    "sheet_num_list": "卡片编号",
    "transition": "跳转",
    "layout_id": "版式编号",
    "layout": "版式",
    "tag": "标签",
    "section": "分区",
    "track": "奖励轨",
    "req": "条件",
    "option": "选项",
    "imagePath": "图片路径",
    "location": "位置",
    "star_count": "星数",
    "get_text": "获得文本",
    "list_text": "列表文本",
    "event_type": "活动类型",
    "need_chapter": "所需章节",
    "contain_chapters": "包含章节",
    "bgm_id": "背景音乐",
    "questTerms": "关卡期间",
    "unitTerms": "角色期间",
    "phaseTerms": "阶段期间",
    "period_key": "期间键",
    "enclosure_id": "附带编号",
    "guide_type": "引导类型",
    "unlock_item_name": "解锁道具名",
    "unlock_item_text": "解锁道具说明",
    "prog_point_name": "进度点名",
    "prog_point_text": "进度点说明",
    "dupli_point": "重复点数",
    "dupli_category": "重复类别",
    "dupli_name": "重复货币",
    "needStone": "所需石",
    "view_time_span": "显示时长",
    "sub_window_resource": "子窗口资源",
    "system": "系统",
    "icon_text": "图标文字",
    "illustrator": "画师",
    "cv": "声优",
    "header_title": "页眉标题",
    "profile": "档案",
    "is_x": "成人内容",
    "change_voice": "换声音",
    "skin_id": "皮肤编号",
}


ORDER = {name: position for position, name in enumerate(TABLES)}


def grouped(name: str) -> tuple[str, str]:
    return TABLES.get(name, (OTHER, name))


# Tables are listed the way an operator thinks of them, the main table of each kind first.
def order(name: str) -> int:
    return ORDER.get(name, len(ORDER))


def label(name: str) -> str:
    return TABLES.get(name, (OTHER, name))[1]


def field_label(path: str) -> str:
    leaf = re.split(r"[.\[]", path)[-1].rstrip("]")
    return FIELDS.get(leaf, leaf)


FLAGS = frozenset(
    {
        "r18",
        "always",
        "salable",
        "is_normal_hide",
        "isMainChapter",
        "is_x",
        "change_voice",
    }
)


def flagged(leaf: str) -> bool:
    return (
        leaf in FLAGS
        or leaf.startswith("is_")
        or (leaf.startswith("is") and leaf[2:3].isupper())
    )


def kind(path: str, value) -> str:
    leaf = re.split(r"[.\[]", path)[-1].rstrip("]")
    if type(value) is bool or (
        type(value) is int and value in (0, 1) and flagged(leaf)
    ):
        return "flag"
    if type(value) in NUMBERS:
        return "number"
    if type(value) is str:
        if DATE.match(value):
            return "datetime"
        if RESOURCE.match(value):
            return "resource"
        return "textarea" if "\n" in value else "text"
    return "list" if isinstance(value, list) else "group"


def row_title(row: dict) -> str:
    for name in TITLES:
        value = row.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip().splitlines()[0]
    return ""
