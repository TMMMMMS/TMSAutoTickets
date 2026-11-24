# 在 Mac 上用 Android 真机值守大麦开售

Mac 运行 Appium 和本项目的 Android V2 脚本。手机保持在大麦目标演出详情页。正式值守时，你在开售前确认一次，程序到点读取购票入口并核对指定场次、票档、张数和观演人。订单生成后，打开大麦的订单列表自行付款。

## 1. 准备手机和 Mac

1. 在手机开发者选项开启 USB 调试，用数据线连接 Mac，解锁并允许这台电脑调试。在大麦登录账号，提前添加实名观演人。
2. 在大麦手动打开目标演出详情页，核对城市、演出日期、票档与**开抢时间**。即使是同一个巡演，切换城市也会改变购票目标。
3. 让手机保持解锁、亮屏和大麦前台。可在 Android 开发者选项开启充电时保持唤醒。Mac 接电、保持开盖，有网络连接，并启用系统自动设置日期与时间。
4. 本机已配好项目虚拟环境、Android SDK、Appium 3.1.0 和 UiAutomator2 5.0.5。换 Mac 时先安装 Android SDK Platform-Tools、兼容的 JDK、Poetry、Node 24.x 和提供 `swiftc` 的 Xcode 命令行工具。在项目目录运行 `poetry install`，再安装 `npm install -g appium@3.1.0` 和 `appium driver install uiautomator2@5.0.5`。若 SDK 不在默认位置，设置 `ANDROID_HOME`。多设备连接时可用 `ANDROID_SERIAL` 指定设备。

上游 README 介绍了 USB 调试、登录和 Appium 安装。本地版本用下列脚本启动，票档按完整文字匹配。部分控件不提供文字时，在 Mac 上用系统 Vision 识别。使用远程 Appium 时，将配置中的 `server_url` 指向服务端，并确认客户端 ADB 与服务端都能访问同一台手机。[上游 README](https://github.com/WECENG/ticket-purchase#readme) · [上游 Android V2 说明](https://github.com/WECENG/ticket-purchase/blob/HEAD/damai_appium/app.md)

## 2. 终端 A 启动 Appium

```bash
cd /Users/wuyongwei/Desktop/ticket-purchase-master
./start_appium.sh
```

看到服务监听 `127.0.0.1:4723` 后保留终端。已有服务在该地址运行时，直接进入下一步。

## 3. 终端 B 填写本场配置

```bash
cd /Users/wuyongwei/Desktop/ticket-purchase-master
cp -n damai_appium/xian.example.json damai_appium/my-show.json
open -e damai_appium/my-show.json
```

示例使用那英西安站的预售页。大麦显示 **2026 年 11 月 24 日 10:08 开抢**，**12 月 26 日 19:00 演出**。请按你要购买的场次逐项填写并保存。配置使用标准 JSON。

| 配置项 | 填写方式 |
|---|---|
| `keyword`、`city` | 详情页的完整演出名称和城市。示例为西安站。 |
| `date`、`session_time` | 演出日期与时间，例如 `2026-12-26`、`19:00`。 |
| `sale_at` | 按北京时间填写到秒，例如 `2026-11-24 10:08:00`。 |
| `price` | 把示例占位文字换成大麦显示的完整票档，包含区域和金额。同价不同区域需区分。 |
| `users`、`ticket_count` | 换成大麦已添加的观演人显示姓名，每人一张。例如两人就填两个不同姓名和数字 `2`。 |
| `retry_minutes` | 从开售起计算，填 5 至 10 的整数。示例填 5，即 10:13 截止。10:10 启动时只剩 3 分钟。 |
| `if_commit_order` | `false` 为演练。正式购买改为 `true`，启动后还需在终端确认。 |

原有即时购买配置可继续使用。省略 `sale_at` 和 `retry_minutes` 时按当前页面购票。裸跑脚本默认读取 `damai_appium/config.jsonc`；下面的每条购买命令都显式选择 `my-show.json`。

## 4. 检查环境、通知与演练

```bash
./check_environment.sh --config damai_appium/my-show.json
./start_ticket_grabbing.sh --test-notification
./start_ticket_grabbing.sh --dry-run --config damai_appium/my-show.json
```

环境检查应显示 Appium 已就绪、Android 版本和大麦安装状态。确认 Mac 能看到“大麦通知测试”。若终端显示已请求通知但桌面没有横幅，检查 macOS「系统设置 → 通知」中的脚本通知权限，以及专注模式的通知设置。

预售阶段看到 **“已就绪”** 表示脚本已建立真机会话，核对了目标详情页和可读的开售时间。可按 **Ctrl+C** 结束这次演练。若保持运行，演练同样等到开售，成功选票后停在确认页，提交次数为零。从演练切换到正式值守前，先结束演练并让手机回到目标详情页。

## 5. 正式值守与付款

将个人配置的 `if_commit_order` 改为 `true` 并保存。在终端 B **提前**执行：

```bash
caffeinate -i ./start_ticket_grabbing.sh --config damai_appium/my-show.json
```

读清终端列出的场次、票档、人数、姓名和北京时间，输入 `y`。看到 **“已就绪”** 后保留两个终端，保持手机前台。开售时终端记录首轮检查时间、首次识别可购时间以及发起首次购买点击的时间。实际响应受大麦放票、网络和手机渲染影响。

| 终端或通知结果 | 接下来怎么做 |
|---|---|
| 订单已生成 | 立即到大麦「我的 → 订单」核对场次和金额，按订单期限付款。 |
| 目标缺票，正在重试 | 保持值守。程序按原条件检查回流票；已识别的排队页面会保留队列位置。 |
| 提交结果待核实 | 先手动查看大麦订单，避免重复购买。 |
| 余票检查时间已到 | 本次窗口已结束，程序停止发起购买。 |
| 需要验证、登录或核对页面 | 查看手机和终端提示，手动接手。 |

通知发送失败时终端会保留结果。按 Ctrl+C 可取消等待。手机上的官方订单记录是核实付款状态的依据。
