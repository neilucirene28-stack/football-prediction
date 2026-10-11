# 北单独立研究模型v13 导入状态

## 导入信息
- **分支**: `codex/beidan-bd1-v13-import`
- **基线**: `origin/v2` @ 6911e0e
- **导入时间**: 2026-10-09T12:15Z
- **导入执行**: Muse（用户授权）

## 附件校验
- **ZIP路径**: `workspace/user/files/beidan-bd1-shadow-v13-l1-validation.zip`
- **ZIP大小**: 6,656,707 字节 ✓
- **ZIP SHA256**: `24842ed2e22339c215f588aa0073f20bc56cd8cab4876abde7d82c51a2349678` ✓
- **包版本**: bd1-v13-research
- **production_eligible**: false

## 逐文件校验
- **MANIFEST文件数**: 299
- **校验通过**: 299
- **校验失败**: 0
- **写入目录**: `beidan_independent/`（原样，未修改算法/参数）

## 独立测试
- **命令**: `PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_beidan_*.py'`
- **结果**: 80 tests, OK (3.247s)
- **Codex复现**: 80项已由Codex复现通过

## 约束声明
- 未改算法、参数、旧engine、既有采集数据、生产配置
- 未合并到v2，未部署，未标为生产
- 179池中5场来源球队候选、174场数据阻断
- 尚无实战Brier上线证明，**不能部署**

## 文件清单
见包内 `MANIFEST.json`（299文件逐项SHA256）
