# 分作用域读盘知识库

本期根据用户提供的《河洛紫微斗数》PDF 与《紫微斗数全书》DOC，整理 65 条原创转述。覆盖生年本命、所选大运及指定农历流年需要的方法、星情、宫位与飞化路径；不是两书逐字数字化或所有古法的完整实现。原书与提取文本留在本地，不随插件分发。

[sources.json](sources.json) 记录文件名、SHA-256、流派及定位口径；[cards.json](cards.json) 为程序读取的正文。PDF 使用物理页码，DOC 使用篇名及正文非空段序号（目录计入，共 2306 段）。来源指向所提供的文件版本，未核验出版版本。遇到文本讹误、图中文字缺损或不同取法，保留限制，不补造原文。

| 字段 | 含义 |
| --- | --- |
| `id`、`title` | 稳定条目 ID 与标题 |
| `source`、`locator`、`school` | 来源 ID、页码／段落与流派 |
| `scopes` | `natal`、`decade`、`annual` 的适用集合 |
| `topics` | 所问主题；空数组表示各主题通用 |
| `stars` | 适用星曜 ID；空数组表示不以特定星曜筛选 |
| `requires` | 必须具备的程序计算能力 |
| `conditions` | 主 AI 必须核对的适用条件 |
| `summary` | 条目总结；传统取象及条件解释 |
| `exceptions` | 例外、不同口径及不得推出的结论 |
| `priority` | 检索顺序，不是预测置信度 |

`KnowledgeBase.query` 先按作用域、主题、盘中星曜筛选，再检查计算能力，最后按优先级分页返回。未满足的能力列入 `unavailable_rules`，不返回相应正文用于推断。每次默认 24 条，可通过 `next_offset` 继续查询；模型应按问题需要读取后续条目。自然语言 `conditions` 由主 AI 核对，不能把“检索到了”当“全部条件已满足”。

两书分别保留全书星情／三方与河洛宫干飞化的口径。例如河洛各层沿用本命宫干、不使用小限，与当前引擎已有的年干四化和小限不是同一个规则；全书独立流羊流陀等尚未生成，相关条目不可用。现代解释限于有条件的传统象征，不沿用古籍的生死断语、医疗结论、性别成见或收益保证。

程序依据来自 `analysis_facts.py`：三方四正、一基地支、相对第九宫气数位、不同层宫名、四化来源和落点。不会修改 `engine.py`。年度分析只保留大限／流年相关层，日期盘仍可正常排盘，但月日时不参与本期解释。

离线检查某张已导出的虚构演示盘：

```sh
python scripts/preview.py "1990-06-15 08:30 男 流年=2026" --json --output .dev/chart.json
python scripts/query_knowledge.py .dev/chart.json --scope annual --topic career --output .dev/reading-context.json
```

运行时由 `ziwei_analysis_context` 建立本人分析，`ziwei_query_knowledge` 按分析 ID 分页检索。首次查询与之后读到的条目会保存版本及内容快照，升级知识库不改写旧案依据。版本变化后，继续追问使用旧快照；若要新增当前版本条目，应另建分析记录。
