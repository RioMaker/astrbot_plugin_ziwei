# 来源与第三方依赖

插件的 Python 适配层、纯计算结构、渲染代码与测试为独立实现。

- 用户提供的本地《寻龙者-紫微斗数课件》：2.4.0 起作为默认安星校勘依据。只分发原创对照说明、来源指纹和计算回归，不分发课件、提取全文或截图。文件版本及异文见[校勘记录](references/XUNLONGZHE_REVIEW.md)和[来源清单](references/xunlongzhe_sources.json)；没有核验讲师身份、授权范围或发行版本，插件许可不授予课件的额外权利。

- `lunar-python==1.4.8`：历法依赖，[上游](https://github.com/6tail/lunar-python)，[MIT 许可](https://github.com/6tail/lunar-python/blob/master/LICENSE)。通过 requirements 安装，不复制包源码。
- Pillow：渲染依赖，[上游与许可](https://github.com/python-pillow/Pillow/blob/main/LICENSE)，通过 requirements 安装。
- 规则数值表、星曜名和固定回归数据：来自用户指定会话对文墨天机 2.5.9 的提取资料，分析日期 2026-10-06。来源与行号见 [provenance.json](references/provenance.json)。插件代码的许可不为原程序或提取资料授予额外权利。
- 真太阳时独立实现 Meeus 太阳参数与均时差近似，参考 [NOAA 计算说明](https://gml.noaa.gov/grad/solcalc/calcdetails.html)，没有复制其计算器代码。
- 没有附带原程序、反编译源码、UI 图片或字体。中文字体从运行环境或配置路径读取，遵循所用字体许可。
