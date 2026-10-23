# 锦纷资产管理系统 v2.5.0

**首个开源版本** · 面向公司、团队与机构的 Windows 原生桌面资产管理工具

让每一项资产都有清晰的去向 —— 整合资产台账、员工部门、领用归还、调拨、维修、采购审批、盘点、折旧统计与操作审计，原生 Qt 界面，开箱即用。

---

## 📦 下载

| 文件 | 说明 | 大小 | SHA256 |
| :--- | :--- | ---: | :--- |
| `installer_cn.exe` | **推荐**。安装版：创建开始菜单入口，可选桌面快捷方式，内置卸载器 | 62.7 MB | `47001142060449349bbd8c13c1f4d20d4db8b42151807b3ff20927515604e7a6` |
| `portal_cn.exe` | 便携版：单文件免安装，双击即用 | 61.7 MB | `b6d9e4707963df4828347227c995e16c02c7998325b2cedf471266169dfe3ede` |

两个版本功能完全相同，默认共用当前 Windows 用户的本地数据库。

> **系统要求**：Windows 10 / 11，64 位。
> 软件暂未进行代码签名，首次运行如遇 SmartScreen 提示，选择「仍要运行」；下载后请核对上方 SHA256 校验值。

---

## ✨ 本版亮点

### 🔐 出厂固定的内置管理员账号
- 用户名固定 `owner`，显示名「内置管理员」，**口令校验值在编译时写死**
- 软件发给谁、装在哪台电脑、换用哪份数据库，这个账号都一致
- 口令、角色、启用状态**不能在软件内修改**，也不能被停用或删除
- 数据库中的记录若被篡改（改口令 / 降级 / 停用 / 试错锁定），**下次登录自动恢复出厂设置**并写入操作审计

### 🌐 局域网数据共享（免装数据库）
- 管理员电脑一键作主机，其他电脑填地址 + 端口 + 访问口令即可共用同一份数据
- 主机显示在线客户端列表与连接日志，支持「仅局域网 / 允许公网访问」开关
- 四层安全：仅局域网地址 → HMAC-SHA256 挑战应答（口令不明文传输）→ SQL 语句白名单（仅单条参数化读写）→ 同地址连错 5 次暂停 10 分钟

### 📊 完整业务闭环
资产台账 · 领用归还 · 员工调拨 · 位置移动 · 送修维修 · 报废 · 采购申请与审批分离（禁止自审）· 盘点快照与差异登记 · 直线法折旧 · 二维码资产标签 · Excel/CSV 导入导出（模板下载 + 预览校验 + 错误行报告）· 完整备份恢复与每日自动备份

### 🛡️ 权限与审计
43 项细粒度权限 · 角色矩阵 · 数据范围隔离 · 业务层二次复查 · 全程操作审计

---

## 🚀 首次使用

1. 双击 EXE 打开登录页，用户名已自动填好 `owner`
2. 输入发行方提供的**内置管理员口令**进入系统
3. 为同事开通账号：切到「注册」选项卡。**新注册账号一律为只读用户**且立即可登录；管理员身份只能由内置管理员在「账号与权限」中授予
4. 建议按顺序初始化：**部门 → 员工 → 供应商 → 资产**

> ⚠️ 明文口令不随软件分发，软件内也不提供找回或重置。首次部署后请立即妥善保管，并与完整备份一并离线保存。

---

## 🔧 从源码运行

```bash
git clone https://github.com/jingpanlong/jinfen-asset-management.git
cd jinfen-asset-management
pip install -r source/requirements.txt
python source/app.py
```

本项目共 **162 项单元测试**全部通过（含 44 项局域网共享测试、14 项内置账号测试），运行方式见 [README](https://github.com/jingpanlong/jinfen-asset-management#-从源码运行)。

---

## 📄 许可证

基于 [MIT License](https://github.com/jingpanlong/jinfen-asset-management/blob/main/LICENSE) 开源。
第三方组件：Qt / PySide6（LGPL-3.0）、Python（PSF License）、Pillow、openpyxl、qrcode、psycopg 等，声明见 [licenses/](https://github.com/jingpanlong/jinfen-asset-management/tree/main/licenses)。

---

## 📮 反馈

微信公众号「景同志」 · 小红书「景同学」 · [提交 Issue](https://github.com/jingpanlong/jinfen-asset-management/issues)

如果这个项目对您有帮助，欢迎 Star ⭐
