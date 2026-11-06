"""One source of command help for the image and its plain-text fallback."""

DEMO_BIRTH = "199006150830 男"
HELP_INTRO = "群里发指令，自动返回命盘图片"
HELP_BIRTH_TITLE = "照着发，就能排盘"
HELP_BIRTH_FIELDS = (
    ("/紫微", "排盘指令"),
    *zip(
        DEMO_BIRTH.split(),
        ("出生日期时间 · 公历", "男 / 女"),
        strict=True,
    ),
)
HELP_BIRTH_NOTE = "年月日时分连写12位，时间用24小时制；性别前空一格。"
HELP_OPTIONS = (
    ("农历生日", "日期前加“农历”", "/紫微 农历 199005230830 男"),
    ("看某一年", "末尾加流年", f"/紫微 {DEMO_BIRTH} 流年=2026"),
    ("换成白底", "末尾加主题", f"/紫微 {DEMO_BIRTH} 主题=白天"),
)
HELP_OPTIONS_NOTE = "完整示例，照着填写；出生资料换成要排盘的资料。"
HELP_BIND_COMMAND = f"/紫微 绑定 {DEMO_BIRTH}"
HELP_BIND_NOTE = "同一机器人内各已开启的群通用；普通排盘不会改绑定。"
HELP_PERSONAL_ACTIONS = (
    ("首次绑定", HELP_BIND_COMMAND),
    ("选已有盘", "/紫微 绑定"),
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
            ("/紫微 农历 2023闰02161200 女", "农历请加前缀，闰月请标明“闰”。"),
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
            ("/紫微 绑定", "从自己的起盘记录中选；发送列出的 命盘=编号 指令确认。"),
            ("/我的命盘", "返回已绑定的本命盘；也可用 /紫微 我的命盘。"),
            ("/紫微 流年 2026", "使用绑定资料排农历流年，无需重填生日。"),
            ("/紫微 大运 2", "使用绑定资料排第2个大运，0为童限。"),
            (
                "/紫微 换绑 199207201430 女",
                "明确替换绑定；也可先发 /紫微 换绑 从记录选。",
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
    "绑定会保留当时的本命盘及取法；/人生 未附资料时优先使用绑定。",
    "以上日期均为虚构演示数据，请替换为需要排盘的出生资料。",
    "当前仅支持指令排盘，暂不提供 AI 看盘、AI 排盘或辅助定盘。",
    "别名：/紫薇、/紫微排盘、/紫薇排盘、/ziwei；均支持开、关、状态。",
    "默认示例用 YYYYMMDDHHMM，年月日时分连写12位，月日时分均补齐两位。",
    "支持1900—2100年；兼容YYYY-MM-DD、HH:MM、HH:MM:SS、HHMMSS和十二时辰。",
    "连写支持YYYYMMDDHHMM／YYYYMMDDHHMMSS；省空格格式有误时静默忽略。",
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
        HELP_OPTIONS_NOTE,
        *HELP_NOTES,
    ]
)
