.PHONY: demo test api collect

demo:            ## 跑内置样例比赛的完整预测（无需数据库）
	python3 scripts/demo_predict.py

test:            ## 跑引擎单元测试
	python3 -m pytest tests/ -q

api:             ## 本地启动 API（含简易前端）
	cd api && uvicorn api.main:app --reload --port 8000

collect:         ## 跑一次采集（默认演示源）
	python3 -m collector --source demo --once
