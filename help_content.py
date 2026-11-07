"""One source of command help for the image and its plain-text fallback."""

DEMO_BIRTH = "199006150830 男"
HELP_INTRO = "公历／阳历、农历／阴历均可排盘"
HELP_BIRTH_TITLE = "照着发，就能排盘"
HELP_BIRTH_FIELDS = (
    ("/紫微", "排盘指令"),
    *zip(
        DEMO_BIRTH.split(),
        ("出生日期时间 · 公历", "男 / 女"),
        strict=True,
    ),
)
HELP_BIRTH_NOTE = "年月日时分连写12位，用24小时制；示例里的空格可省。"
HELP_COMPATIBILITY_TITLE = "日期写法"
HELP_COMPATIBILITY_NOTE = "不写历法默认公历；日期转换后再按设置校正真太阳时。"
HELP_COMPATIBILITY = (
    ("公历／阳历", "两种名称通用", "/紫微 阳历 199006150830 男"),
    ("农历／阴历", "也支持中文年月日", "/紫微 阴历 1990年五月廿三日 0830 男"),
    ("农历闰月", "月份前标明“闰”", "/紫微 农历 2023闰02161200 女"),
    ("末尾选历法", "不能与日期前缀冲突", "/紫微 199005230830 男 历法=农历"),
)
HELP_OPTIONS = (
    ("看某一年", "末尾加流年", f"/紫微 {DEMO_BIRTH} 流年=2026"),
    ("换成白底", "末尾加主题", f"/紫微 {DEMO_BIRTH} 主题=白天"),
)
HELP_OPTIONS_NOTE = "完整示例，出生资料换成需要排盘的资料。"
HELP_BIND_COMMAND = f"/紫微 绑定 {DEMO_BIRTH}"
HELP_BIND_NOTE = "同一机器人内各已开启的群通用；普通排盘不会改绑定。"
HELP_PERSONAL_ACTIONS = (
    ("首次绑定", HELP_BIND_COMMAND),
    ("查看本命", "/我的命盘"),
    ("查看流年", "/紫微 流年 2026"),
    ("查看大运", "/紫微 大运 2"),
    ("更换绑定", "/紫微 换绑 199207201430 女"),
    ("解除绑定", "/紫微 解绑"),
)
HELP_ENTRY_COMMAND = "/紫薇"
HELP_COMMANDS = ("/紫薇 help", "/紫薇 帮助")
HELP_COMPACT_COMMANDS = ("/紫薇help", "/紫薇帮助")
HELP_MORE_COMMAND = "/紫薇帮助文字"
HELP_IMAGE_NOTES = (
    "虚构示例，请换成出生资料 · 仅已开启的群可用。",
    "目前只排盘，暂不提供 AI 解读。",
)
HELP_SECTIONS = (
    (
        HELP_BIRTH_TITLE,
        ((f"/紫微 {DEMO_BIRTH}", HELP_BIRTH_NOTE),),
    ),
    (
        HELP_COMPATIBILITY_TITLE,
        tuple(
            (example, f"{label}；{hint}") for label, hint, example in HELP_COMPATIBILITY
        ),
    ),
    (
        "常用选项",
        tuple((example, f"{label}；{hint}") for label, hint, example in HELP_OPTIONS),
    ),
    (
        "更多写法",
        (
            (
                "/ziwei 199006150830 男",
                "支持日期时间连写；也可用 /紫微199006150830男。",
            ),
            (
                "/紫微 阳历 1990年6月15日 0830 男",
                "公历与阳历同义；未注明历法时默认公历。",
            ),
        ),
    ),
    (
        "选择盘面",
        (
            (
                "运限=1  /  流年=2026",
                "在出生资料后任选其一；运限=0 为童限，1—12 为大运。",
            ),
            (
                "主题=夜间  /  主题=白天",
                "深蓝星空或浅色星图；末尾加“文字”可取完整文字盘。",
            ),
            (
                "经度=116.4  时区=8  真太阳时=开",
                "按出生地调整；默认东经120°、UTC+8、真太阳时近似校正。",
            ),
        ),
    ),
    (
        "我的命盘",
        (
            (HELP_BIND_COMMAND, "首次绑定；填写自己的出生资料。"),
            ("/我的命盘", "返回已绑定的本命盘；也可用 /紫微 我的命盘。"),
            ("/紫微 流年 2026", "使用绑定资料排农历流年，无需重填生日。"),
            ("/紫微 大运 2", "使用绑定资料排第2个大运，0为童限。"),
            (
                "/紫微 换绑 199207201430 女",
                "重新填写出生资料后换绑，不支持选择已有命盘。",
            ),
            ("/紫微 解绑", "解除绑定，保留历史起盘记录。"),
        ),
    ),
    (
        "更多指令",
        (
            (HELP_ENTRY_COMMAND, "直接查看帮助图片。"),
            (
                "/紫微 图谱 <出生日期时间> <性别>",
                "也可用 /人生；返回已有规则计算的人生图谱，无 AI 解读。",
            ),
            (
                " 或 ".join(HELP_COMMANDS),
                "查看帮助图片；也可连写为 " + " 或 ".join(HELP_COMPACT_COMMANDS) + "。",
            ),
            (HELP_MORE_COMMAND, "完整参数和可复制示例；也可用 /紫薇helptext。"),
        ),
    ),
    (
        "群管理",
        (
            (
                "/紫微 开启",
                "由群主、群管理员或 AstrBot 管理员在要排盘的群里发送一次。",
            ),
            (
                "/紫微 关闭",
                "由群主、群管理员或 AstrBot 管理员在本群操作。",
            ),
            ("/紫微 状态", "所有群默认未开启；仅已授权群能排盘，私聊仅能看帮助。"),
        ),
    ),
)
HELP_NOTES = (
    HELP_BIND_NOTE,
    "绑定与换绑均须填写出生资料，每次均留命盘和操作历史；后台可查看。",
    "绑定会保留当时的本命盘及取法；/人生 未附资料时优先使用绑定。",
    "以上日期均为虚构演示数据，请替换为需要排盘的出生资料。",
    "当前仅支持指令排盘，暂不提供 AI 看盘、AI 排盘或辅助定盘。",
    "别名：/紫薇、/紫微排盘、/紫薇排盘、/ziwei；均支持开、关、状态。",
    "默认示例用 YYYYMMDDHHMM，年月日时分连写12位，月日时分均补齐两位。",
    "农历中文日期支持正月、冬月、腊月、初一、廿三等写法；年份仍填四位数字。",
    "农历／阴历先转成公历钟表日期，再按经度、时区与真太阳时开关校正；不会按历法强制开关校正。",
    "支持1900—2100年；兼容YYYY-MM-DD、HH:MM、HH:MM:SS、HHMMSS和十二时辰。",
    "连写支持YYYYMMDDHHMM／YYYYMMDDHHMMSS；省空格格式有误时静默忽略。",
    "可连写：/紫薇绑定199006150830男、/紫薇流年2026文字、/我的命盘文字主题=白天。",
    "大限=、大运=与运限=同义；运限不与流年、流盘、流时混用。",
    "日期流盘：出生资料后加 流盘=20261006 流时=1000。",
    "规则选项：闰月=current|next|split，子时=next_day|midnight。",
    "默认闰月十五／十六分界、晚子时换日。命盘按本人平台实例、群、用户归档。",
)
HELP = "\n".join(
    [
        "紫微斗数排盘",
        HELP_INTRO,
        *(
            line
            for title, rows in HELP_SECTIONS
            for line in (
                f"【{title}】",
                *(f"{command}：{description}" for command, description in rows),
            )
        ),
        HELP_COMPATIBILITY_NOTE,
        HELP_OPTIONS_NOTE,
        *HELP_NOTES,
    ]
)
