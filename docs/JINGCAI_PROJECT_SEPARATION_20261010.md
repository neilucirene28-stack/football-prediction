# 竞彩项目专用入口与交付范围

本文记录入口隔离修正。第五轮牌数、半场与MC改动及最新验收见`JINGCAI_CARDS_HT_MC_REPAIR_20261010.md`；项目隔离约束继续适用。

用户要求竞彩与北单各自处理。本轮交付只应用到竞彩项目，不更新北单独立项目。前版所说“北单输出一致”仅指修改前后的回归对照，不表示竞彩与北单预测一致；旧报告的两类测试数字是当时兼容性验收，不能当作竞彩项目的独立测试数。

## 本轮隔离修正

1. 新增`engine.jingcai_predictor.predict`，所有竞彩API与项目内非北单预测脚本切到此入口。参数固定竞彩，拒绝`model="beidan"`、指向其他项目的输入/配置标识及`beidan*`专用字段或配置项。
2. 配置自动包含`project_scope="jingcai"`，纳入基础模型与校准模型版本哈希。校准必须匹配该竞彩专用基础版本；旧双模式门面的校准来源元数据不能直接冒用。
3. 输出明确标识`model/project_scope="jingcai"`，删除旧门面遗留的空`beidan`字段。默认概率算法没有因此变成北单算法，也没有新增北单校准调用。
4. 竞彩封存拒绝其他项目标识。竞彩复盘SQL在`ORDER BY/LIMIT`之前过滤`payload.result.model`与项目范围，北单记录不会占用竞彩接口的2000条查询额度；旧合规竞彩记录仍可按自己的版本描述评分。
5. 撤回上一包对`tests/test_beidan.py`的日期修正，恢复上传原件。累计补丁禁止包含任何`beidan`专用文件改动。
6. 新交付验证样本只包含竞彩；不再随包包含北单合成预测或北单回归脚本。

原上传工程本身含旧双模式门面与北单模块。为保持源代码可合并，本包保留这些原文件的兼容接口，数学实现复用旧核心中的**竞彩分支**；竞彩API和预测脚本的实际调用已固定到专用入口。此轮没有将整个原仓库物理拆成两个仓库，也没有部署北单项目。本包的导入目标必须是竞彩项目。

## 使用

```python
from engine.jingcai_predictor import predict, PredictError

result = predict(jingcai_payload, jingcai_config)
```

`asof`仅供明确标记的离线重放；真实预测仍须实际生成并封存在开球前。正式竞彩整数让球来自竞彩比赛输入，不能用北单在售数据或亚盘线代替。校准来源、评分版本和学习样本必须属于竞彩；缺少来源标记时，不应凭字段形状推断它是竞彩数据。

## 验证

新增17项隔离用例，覆盖错误模式/输入/参数在进入引擎前被拒、API不打开数据库、无北单输出、版本作用域、默认概率未变、旧入口校准版本被拒、合法竞彩校准声明可用、账本项目范围与SQL预过滤。

竞彩与通用依赖检查：391项通过，1项因未安装penaltyblog跳过；混合测试文件中的1个北单运行用例不在本轮验收范围。采集器70项通过。运行命令：

```bash
python3 -m pytest tests/ -q -rs --ignore-glob='*test_beidan*.py' --deselect=tests/test_ipf_calibration.py::TestPredictorConsistency::test_beidan_path_also_calibrated
python3 -m pytest collector/tests/ -q -rs
python3 /path/to/verification/compare_jingcai_probes.py --project-root .
```

验证中“北单”字段只作为拒绝边界的测试输入，不运行北单预测或拟合。合成竞彩回归只用于默认概率与矩阵衍生一致性，不能解释为实际赛事准确率提升。SQL检查使用测试替身，尚未运行真实数据库集成验证。

前三轮竞彩输入、矩阵、封存与复盘修复继续保留。当前入口与项目范围以本文为准；历史阶段验收见对应报告。
