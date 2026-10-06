# 紫微斗数插件维护

单独打开本仓库时，先读取 `../AGENTS.md` 及共享开发基准；独立 worktree 需提供实际基准路径。

- 插件标识、目录和仓库名称统一为 `astrbot_plugin_ziwei`。
- `engine.py` 保持纯计算，输入与输出使用一基地支索引；历法、安星、渲染和 AstrBot 适配分层。
- 规则来源及差异见 `references/IMPLEMENTATION.md`；不得将公式样例通过宣称为原程序 GUI 或线上验证。
- 更新规则时增加能揭示日期边界、配置交叉污染或落星错误的回归用例，不自动重写固定期望。
- 运行 Ruff 格式化、检查和 pytest，核对图片后本地中文提交，再按根规则更新 `../plugins.json`。
