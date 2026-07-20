# EcoWise 宿舍助理 - 后端对接文档(前端同学看这份)

> 同学你好,这是后端 L0 阶段交付的代码。本文档说明每个模块返回的数据结构,你可以基于这些结构设计小程序界面。

---

## 一、项目结构

```
Ecowise/
├── config.py                # 配置(凭证/设备清单/阈值/电价)
├── device_client.py         # 涂鸦云 API 调用 + 单位换算
├── violation_detector.py    # 违规电器检测(功率阈值法)
├── billing.py               # 电费分摊
├── energy_history.py        # 历史用电量(今日/本月,本地存储)
└── main.py                  # 主程序入口(命令行菜单)
```

前端只需要关心**模块对外提供的函数和返回的数据结构**,不用管内部实现。

---

## 二、数据结构总览

### 1. 设备实时数据(device_client.get_device_data)

**函数:** `device_client.get_device_data(device_id) -> dict`
**批量:** `device_client.get_all_devices() -> list[dict]`

**返回数据结构:**
```json
{
  "device_id": "6cc488dba84ddfba58ypx2",
  "device_name": "插座1(同学A-电脑桌)",
  "owner": "同学A",
  "online": true,
  "switch_on": true,
  "power_w": 4.2,           // 当前功率,单位 W
  "voltage_v": 226.2,        // 电压,单位 V
  "current_ma": 47,          // 电流,单位 mA
  "energy_kwh": 0.003,       // 累计电量,单位 度(kWh)
  "energy_wh": 3.0,          // 累计电量,单位 Wh
  "timestamp": "2026-07-08 14:30:00"
}
```

**前端展示建议:**
- 卡片式展示每个插座的实时数据
- 用 `power_w` 做实时功率仪表盘
- 用 `energy_kwh` 显示累计电量
- `online` 和 `switch_on` 控制开关图标颜色

---

### 2. 违规电器检测(violation_detector)

**函数:** `violation_detector.detect_all_devices(devices_data) -> list[dict]`

入参是 `device_client.get_all_devices()` 的返回值,在每个设备数据上多加一个 `violation` 字段:

```json
{
  // ...上面的所有字段
  "violation": {
    "level": "normal",           // "normal" | "warning" | "violation"
    "title": "用电正常",          // 简短标题
    "message": "当前功率 4.2W,处于安全范围。",  // 详细说明
    "power_w": 4.2
  }
}
```

**level 三种等级:**
| level | 含义 | 前端建议颜色 |
|---|---|---|
| `normal` | 正常 | 绿色 |
| `warning` | 限电预警(>450W) | 黄色 |
| `violation` | 违规告警(>800W) | 红色 |

**前端展示建议:**
- 用红/黄/绿色卡片区分三种状态
- 违规时弹出醒目提醒
- `message` 可以直接显示给用户

---

### 3. 电费分摊(billing.calc_person_bill)

**函数:** `billing.calc_person_bill(devices_data) -> dict`

```json
{
  "total_kwh": 0.003,         // 全宿舍总电量(度)
  "total_yuan": 0.0,           // 全宿舍总电费(元)
  "per_person": [              // 按金额从高到低排序
    {
      "owner": "同学A",
      "kwh": 0.003,           // 该室友累计电量(度)
      "wh": 3.0,              // 该室友累计电量(Wh)
      "yuan": 0.0,            // 应交电费(元)
      "devices": ["插座1(同学A-电脑桌)"]
    }
  ],
  "price_per_kwh": 0.55       // 电价(元/度)
}
```

**前端展示建议:**
- 用柱状图展示每人电费
- 顶部显示全宿舍总电费
- `yuan` 字段保留 2 位小数

---

### 4. 历史用电量(energy_history)

#### 4.1 今日用电量(分小时)

**函数:** `energy_history.get_today_energy(device_id) -> dict`

```json
{
  "date": "2026-07-08",
  "total_kwh": 0.002,        // 今日总电量(度)
  "hourly": [                // 按小时明细
    {"hour": 9, "kwh": 0.001},
    {"hour": 10, "kwh": 0.001}
  ]
}
```

**前端展示建议:**
- 用折线图展示 24 小时用电趋势
- X 轴是小时(0-23),Y 轴是电量(度)

#### 4.2 本月用电量(分日,做趋势图用)

**函数:** `energy_history.get_month_energy(device_id) -> dict`

```json
{
  "month": "2026-07",
  "total_kwh": 0.005,        // 本月总电量(度)
  "daily": [                 // 按日明细,包含本月每一天(没数据的也是 0)
    {"date": "2026-07-01", "kwh": 0.0},
    {"date": "2026-07-02", "kwh": 0.001},
    {"date": "2026-07-03", "kwh": 0.002}
  ]
}
```

**前端展示建议:**
- **重点**:用 `daily` 数组做柱状图/折线图
- X 轴是日期, Y 轴是当日用电量(度)
- 数据会从今天开始往前累积,前几天是 0 是正常的

---

## 三、单位换算说明(重要)

| 字段 | 单位 | 说明 |
|---|---|---|
| `power_w` | W | 当前功率,涂鸦原始值 × 0.1 |
| `voltage_v` | V | 电压,涂鸦原始值 × 0.1 |
| `current_ma` | mA | 电流,无需换算 |
| `energy_kwh` | 度(kWh) | 累计电量,涂鸦原始值 × 0.001 |
| `energy_wh` | Wh | 累计电量,energy_kwh × 1000 |

> **为什么 App 显示 0.003 而代码里是 3?**
> App 显示的 0.003 单位是**度(kWh)**,代码里 3 单位是 **Wh**。
> 0.003 度 = 3 Wh,两个值是相等的,只是单位不同。

---

## 四、数据采集说明

### 历史数据是本地存储的

`energy_history.py` 不依赖涂鸦统计接口(那个需要开通权限),改用本地 SQLite 存储:
- 每次调用 `record_once(device_id)` 会把当前累计电量存进 `energy_log.db`
- 今日用电 = 当前累计电量 - 今日最早记录的累计电量
- 本月每日用电 = 当天最后一条 - 当天第一条

**前端同学请注意:**
- 今天第一次跑,今日明细只会显示从现在开始的数据
- 明天再跑,才能看到完整一天的用电量
- 跑满一周,本月每日明细才有意义

---

## 五、前端如何调用后端

### 目前阶段(L0):Python 函数调用

后端目前是 Python 函数,前端不能直接 fetch。两种对接方式:

#### 方式 A:后端封装成 HTTP API(推荐,L1 阶段做)

```python
# 后端用 Flask 提供 HTTP 接口(后续会做)
from flask import Flask, jsonify
import device_client, billing, energy_history

app = Flask(__name__)

@app.route("/api/devices")
def api_devices():
    return jsonify(device_client.get_all_devices())

@app.route("/api/bill")
def api_bill():
    devices = device_client.get_all_devices()
    return jsonify(billing.calc_person_bill(devices))
```

前端就能:
```javascript
fetch("http://后端ip:5000/api/devices")
  .then(r => r.json())
  .then(data => { /* 渲染小程序 */ })
```

#### 方式 B:前端同学直接看本对接文档

先按本文档的数据结构设计小程序界面,等后端 HTTP API 做好后,数据结构完全一致,直接对接。

---

## 六、运行方式

```bash
cd Ecowise
pip install -r requirements.txt    # 安装依赖
python main.py                     # 跑主程序
```

选菜单:
- 1) 查看所有插座实时数据
- 2) 违规电器检测
- 3) 查看电费分摊账单
- 4) 全部跑一遍
- 5) 查看今日用电量(分小时)
- 6) 查看本月用电量(分日)

---

## 七、后续计划(L1)

后端 L1 会新增:
1. 温湿度/光照传感器数据接入
2. 作息时间自动记录(基于功率变化推断"人在不在宿舍")
3. AI 对话助手(涂鸦智能体)
4. 熬夜提醒推送
5. HTTP API 封装(Flask),让前端真正能调用

数据结构会保持兼容,前端不用担心返工。
