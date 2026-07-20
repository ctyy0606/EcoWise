# EcoWise 宿舍助理 - L1 阶段交接文档

## 项目概述

EcoWise 是一款宿舍用电管理系统，帮助学生安全用电、节约能源。本项目已完成 L0（CLI版本）和 L1（Web前端+后端API）阶段开发。

**当前状态**：L1 阶段功能已实现，但硬件（涂鸦智能插座）尚未连接，大部分功能无法进行端到端测试。

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | HTML5 + CSS3 + JavaScript (原生) |
| 后端 | Python 3.x + Flask |
| 数据库 | SQLite |
| 云服务 | 涂鸦云开放平台（API）、阿里云通义千问（AI） |
| 图标 | Emoji |

---

## 项目结构

```
d:\VS\code\Ecowise\
├── main.py              # L0 CLI入口（保留）
├── web_server.py        # L1 Flask后端入口
├── config.py            # 全局配置
├── requirements.txt     # Python依赖
├── energy_log.db        # SQLite数据库（运行时生成）
├── devices.json         # 网页添加的设备配置
├── templates/
│   └── index.html       # 前端单页应用
└── 模块文件:
    ├── device_client.py     # 涂鸦云API封装
    ├── energy_history.py    # 用电历史记录
    ├── violation_detector.py # 违规检测
    ├── carbon.py            # 碳排放计算
    ├── schedule_analyzer.py # 作息分析
    ├── appliance_fingerprint.py # 电器识别
    ├── weekly_report.py     # AI周报生成
    └── user_auth.py         # 用户认证与空间管理
```

---

## L1 阶段变更清单

### 1. config.py - 新增配置项

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `TEST_MODE` | `False` | 测试模式开关，开启后模拟高功率数据 |
| `TEST_POWER_W` | `1200` | 模拟功率值（W），1200可触发违规断电 |
| `CARBON_EMISSION_FACTOR` | `0.5777` | 碳排放因子（kgCO₂e/kWh） |
| `AUTO_POWER_OFF_COOLDOWN_MINUTES` | `10` | 自动断电冷却期（分钟） |
| `SMART_POLICY` | {...} | 智能断电策略参数 |
| `SCHEDULE` | {...} | 作息分析参数 |

### 2. device_client.py - 添加测试模式

- 在 `get_device_data()` 函数中添加了 `TEST_MODE` 判断逻辑
- 测试模式下返回模拟的高功率数据，无需真实硬件即可测试自动断电功能

### 3. web_server.py - 新增API接口

| API路径 | 方法 | 说明 |
|---------|------|------|
| `/api/weekly_report/space/generate` | POST | 生成空间周报 |
| `/api/weekly_report/space/latest` | GET | 获取空间最新周报 |

### 4. weekly_report.py - 新增空间周报功能

- `generate_space_report(space_id, user_phone)` - 生成空间周报
- `get_latest_space_report(space_id)` - 获取空间最新周报
- 数据库表新增 `space_id` 字段（含迁移逻辑）

### 5. carbon.py - 日期范围调整

- `get_week_carbon()` 改为按周一到周日统计（原逻辑为最近7天）

### 6. schedule_analyzer.py - 日期范围调整

- `get_week_schedule()` 改为按周一到周日统计

### 7. templates/index.html - 前端UI变更

#### 实时数据Tab
- 添加电器识别卡片（显示电器名称、置信度、风险等级、建议）
- 无数据时显示"请先插上用电设备"提醒
- 自动断电卡片添加"收起"按钮
- 电器识别卡片添加"收起"按钮

#### 碳排放Tab
- 碳排放因子和等效植树旁添加问号解释图标
- 点击图标显示弹窗解释

#### 作息Tab
- 添加睡眠判断说明（功率<30W持续30分钟判定入睡）
- 睡眠事件显示入睡时间、时长、醒来时间
- 睡眠趋势图Y轴标签上移（避免遮挡）

#### 通知系统
- 通知铃铛移到"退出"按钮下方新行
- 通知面板添加"收起"按钮

#### AI助手Tab（周报）
- 添加"个人周报"/"空间周报"切换按钮
- 空间周报需选择空间
- 数据汇总改为卡片式网格布局（总用电、电费、碳排放、违规次数）

---

## 测试模式使用说明

### 模拟大功率电器测试自动断电

1. 编辑 `config.py`，设置：
   ```python
   TEST_MODE = True
   TEST_POWER_W = 1200  # 超过违规阈值800W
   ```

2. 重启 `web_server.py`

3. 访问前端实时数据页面，即可看到模拟的1200W功率数据，触发违规告警和自动断电

### 恢复正常模式

1. 编辑 `config.py`，设置：
   ```python
   TEST_MODE = False
   ```

2. 重启 `web_server.py`

---

## 已知问题与风险

### 未端到端验证的功能（硬件未连接）

| 功能 | 状态 | 说明 |
|------|------|------|
| 实时功率数据拉取 | ⚠️ 未验证 | 需连接真实涂鸦插座 |
| 自动断电功能 | ⚠️ 未验证 | 需真实硬件测试断电指令 |
| 电器识别 | ⚠️ 未验证 | 需真实电器数据训练模型 |
| 作息分析 | ⚠️ 未验证 | 需真实功率时序数据 |
| 空间成员数据汇总 | ⚠️ 未验证 | 需多用户真实数据 |

### 潜在问题

1. **数据库迁移**：`weekly_reports` 表新增 `space_id` 列，首次运行时会自动添加（通过 ALTER TABLE），但如果旧数据未填充 `space_id`，可能影响查询。

2. **电器识别模型**：当前 `appliance_fingerprint.py` 中的识别规则较为简单，仅基于功率范围判断，需要更多真实数据训练优化。

3. **睡眠判断逻辑**：仅基于功率判断，可能误判（如用户离开宿舍但未断电），建议后续结合温湿度、光照等传感器数据。

---

## 下一步建议

### 待验证功能（需硬件连接后）

1. **连接真实涂鸦插座**，测试实时数据拉取和开关控制
2. **测试自动断电功能**：插入大功率电器验证断电和冷却期逻辑
3. **测试电器识别**：插入不同电器（电脑、手机充电器、吹风机等）验证识别准确率
4. **测试作息分析**：持续运行一周，验证睡眠事件识别是否准确

### 待优化功能

1. **电器识别模型优化**：收集更多电器功率数据，改进识别算法
2. **睡眠判断优化**：增加温湿度、光照传感器辅助判断
3. **通知系统完善**：添加推送通知（微信/短信）
4. **数据可视化优化**：使用专业图表库（如 Chart.js）替换原生Canvas

### 新功能建议

1. **用电预警推送**：当功率接近阈值时发送微信通知
2. **用电习惯分析**：AI分析用电模式，给出个性化节能建议
3. **设备管理增强**：支持设备分组、定时开关
4. **移动端适配**：优化移动端显示效果

---

## 运行方式

### 启动开发服务器

```bash
cd d:\VS\code\Ecowise
pip install -r requirements.txt
python web_server.py
```

### 访问地址

前端页面：`http://localhost:5000`

---

## 关键配置文件

| 文件 | 说明 |
|------|------|
| `config.py` | 涂鸦云API凭证、设备清单、阈值配置 |
| `devices.json` | 网页端添加的设备配置（运行时自动生成） |
| `energy_log.db` | SQLite数据库文件（运行时自动生成） |

---

## 注意事项

1. **涂鸦云凭证**：`ACCESS_ID` 和 `ACCESS_SECRET` 需要在涂鸦开发者平台申请
2. **通义千问API**：`QWEN_API_KEY` 需要在阿里云百炼平台申请
3. **测试模式**：用于开发测试，正式部署时务必关闭
4. **数据库备份**：定期备份 `energy_log.db` 文件
