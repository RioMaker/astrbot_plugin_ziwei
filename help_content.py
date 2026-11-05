"""One source of command help for the image and its plain-text fallback."""

DEMO_BIRTH = "1990-06-15 08:30 男"
HELP_INTRO = "群里发指令，自动返回命盘图片"
HELP_START = (
    "管理员先开启",
    "/紫微 开启",
    "在要排盘的群里发送一次；已开启可跳过。",
)
HELP_BIRTH_TITLE = "照着发，就能排盘"
HELP_BIRTH_FIELDS = (
    ("/紫微", "排盘指令"),
    *zip(DEMO_BIRTH.split(), ("公历生日", "出生时间", "男 / 女"), strict=True),
)
HELP_BIRTH_NOTE = "换成要排盘的资料；时间用24小时制，每项空一格。"
HELP_OPTIONS = (
    ("农历生日", "日期前加", "农历"),
    ("看某一年", "末尾加", "流年=2026"),
    ("换成白底", "末尾加", "主题=白天"),
)
HELP_OPTIONS_NOTE = "按需添加；选项与出生资料之间留空格。"
HELP_MORE_COMMAND = "/紫微 帮助 文字"
HELP_IMAGE_NOTES = (
    "虚构示例 · 排盘仅限已开启的群，私聊不可用。",
    "目前只排盘，暂不提供 AI 解读。",
)
HELP_SECTIONS = (
    (HELP_START[0], ((HELP_START[1], HELP_START[2]),)),
    (
        HELP_BIRTH_TITLE,
        ((f"/紫微 {DEMO_BIRTH}", HELP_BIRTH_NOTE),),
    ),
    (
        "常用选项",
        tuple(
            (label, f"{position} {option}") for label, position, option in HELP_OPTIONS
        ),
    ),
    (
        "更多写法",
        (
            (
                "/ziwei 199006150830 男",
                "支持日期时间连写；也可用 /紫微199006150830男。",
            ),
            ("/紫微 农历 2023-闰02-16 12:00 女", "农历请加前缀，闰月请标明“闰”。"),
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
        "更多指令",
        (
            (
                "/紫微 图谱 <日期 时刻 性别>",
                "也可用 /人生；返回已有规则计算的人生图谱，无 AI 解读。",
            ),
            (
                f"/紫微 帮助  /  {HELP_MORE_COMMAND}",
                "查看帮助图片，或获取可复制的完整文字说明。",
            ),
        ),
    ),
    (
        "群内授权",
        (
            (
                "/紫微 关闭",
                "由群主、群管理员或 AstrBot 管理员在本群操作。",
            ),
            ("/紫微 状态", "所有群默认未开启；仅已授权群能排盘，私聊仅能看帮助。"),
        ),
    ),
)
HELP_NOTES = (
    "以上日期均为虚构演示数据，请替换为需要排盘的出生资料。",
    "当前仅支持指令排盘，暂不提供 AI 看盘、AI 排盘或辅助定盘。",
    "别名：/紫薇、/紫微排盘、/紫薇排盘、/ziwei；均支持开、关、状态。",
    "支持1900—2100年，时刻可用HH:MM:SS、HHMM、HHMMSS或十二时辰。",
    "连写支持YYYYMMDDHHMM／YYYYMMDDHHMMSS；省空格格式有误时静默忽略。",
    "大限=、大运=与运限=同义；运限不与流年、流盘、流时混用。",
    "日期流盘：出生资料后加 流盘=2026-10-06 流时=10:00。",
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
