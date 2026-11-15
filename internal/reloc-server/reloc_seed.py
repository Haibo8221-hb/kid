#!/usr/bin/env python3
"""Relocation Agent — 任务图初始化（伊斯坦布尔 → 迪拜 2027-01/02）
一次性种子脚本：建表 + 48条任务 + 项目参数。幂等（重复跑先删后插）。
DB: /www/wwwroot/kid/data/reloc/tasks.db （服务器运行时数据，不入公开git）
"""
import sqlite3, os, sys
from datetime import date

DB = '/www/wwwroot/kid/data/reloc/tasks.db'
os.makedirs(os.path.dirname(DB), exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, phase TEXT, title TEXT,
  status TEXT DEFAULT 'todo', owner TEXT,
  due TEXT, depends_on TEXT, risk TEXT DEFAULT 'med',
  human_gate INTEGER DEFAULT 0, docs TEXT,
  source TEXT DEFAULT '', checked TEXT DEFAULT '', notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, topic TEXT,
  conclusion TEXT, source TEXT, checked TEXT, confidence TEXT,
  open_items TEXT DEFAULT '', created TEXT
);
CREATE TABLE IF NOT EXISTS daily_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_date TEXT, kind TEXT,
  overdue_n INTEGER, actions_n INTEGER, researched_n INTEGER, notes TEXT
);
"""

META = {
 'project': '伊斯坦布尔 → 迪拜 全家搬迁',
 'arrival_anchor': '2027-01-15（假设锚点，待HR确认）',
 'work_location': 'Bay Gate Tower, Business Bay, Dubai（通勤半径=选房/托儿所核心约束）',
 'housing': 'HR住房补贴≈房租 → 决策：直接签年约(Ejari)，放弃短租过渡（家属签需要Ejari年约）',
 'daughters_passport': '中国普通护照 → 免签90天/180窗（2025-04使馆新规），假期探访无需陪同签',
 'search_backend': 'tavily',
 'residence_rule': '家属居留签离境>180天自动作废（u.ae）；幼儿与爱人走家属签链，女儿走免签探访',
 'created': '2026-11-15',
}

# (id, phase, title, status, owner, due, depends_on, risk, human_gate, docs, notes)
T = [
 # P0 文件与情报
 ('P0-01','P0','向迪拜HR索要完整清单：entry permit谁办/薪资门槛/补贴确认/NOC要求','waiting_on','海波','2026-11-21','','high',0,'—','补贴已确认≈房租；办公地Bay Gate Tower(Business Bay)'),
 ('P0-02','P0','文件盘点：每份证件签发国+持有人（结婚证/幼儿出生证/女儿出生证/5本护照/学历/无犯罪）','doing','爱人','2026-11-18','','high',0,'全部证件原件','决定认证链条数'),
 ('P0-03','P0','5本护照有效期核查（≥12月为佳，<6月拒绝入境）','todo','爱人','2026-11-21','P0-02','high',0,'护照','幼儿护照最易过期'),
 ('P0-04','P0','中国端认证链：公证→外事认证→阿联酋驻华使馆领事认证（阿联酋不在海牙公约）','todo','爱人/中介','2026-12-15','P0-02','high',0,'结婚证/出生证(如中国签发)','3-6周不可控，全链关键'),
 ('P0-05','P0','土耳其端认证链（如文件在土签发）：土外交部→阿联酋驻安卡拉使馆+宣誓阿语翻译','todo','海波','2026-12-15','P0-02','high',0,'土耳其签发文件',''),
 ('P0-06','P0','英国端认证链（女儿出生证如在英签发）：FCDO→阿联酋驻伦敦使馆','todo','海波/Agent','2026-12-20','P0-02','med',0,'英国签发文件','仅家属签需要；女儿探访不需要'),
 ('P0-07','P0','阿语宣誓翻译（GDRFA材料）','todo','中介','2026-12-20','P0-04','low',0,'—',''),
 ('P0-08','P0','无犯罪/学历/体检等HR附加项对单','todo','海波','2026-12-01','P0-01','med',0,'按HR清单',''),
 # P1 签证链
 ('P1-01','P1','HR发出entry permit（就业签）','waiting_on','雇主HR','2026-12-25','P0-01,P0-08','high',1,'护照/合同','政府系统=HR操作，海波提供材料'),
 ('P1-02','P1','担保资格确认：薪资≥AED4000(或3000+住房)+工资证明+NOC','waiting_on','雇主HR','2026-12-10','P0-01','med',0,'工资证明','住房补贴≈房租→按4000档理解,待HR确认口径'),
 ('P1-03','P1','海波入境→体检→居留签→Emirates ID','todo','海波/HR','2027-01-25','P1-01','high',1,'护照/体检','入境60日内办结否则罚款'),
 ('P1-04','P1','爱人+幼儿：entry permit→居留签→EID→医保','todo','海波担保','2027-02-15','P1-03,P0-04,P3-01','high',1,'认证结婚证/出生证/Ejari','Ejari在先！'),
 ('P1-05','P1','家属签路径研究：境外entry permit vs 免签入境后境内status change','research','Agent','2026-12-15','P0-01','med',0,'—','问HR/代理哪个快'),
 ('P1-06','P1','全家医保：雇主团险覆盖范围+幼儿缺口补齐','todo','海波','2027-02-01','P1-03','med',0,'保单',''),
 # P2 搬迁执行
 ('P2-01','P2','短租vs年约决策','done','海波','2026-11-15','','med',0,'—','决策：直接年约。补贴≈房租+家属签需Ejari年约'),
 ('P2-02','P2','国际搬家：空运/海运决策+报价（海运6-8周）','research','Agent','2026-12-15','P0-01','high',1,'行李清单','付款红线'),
 ('P2-03','P2','机票：海波先遣+全家到达+女儿假期票','todo','Agent','滚动','P1-01','med',1,'护照','付款红线'),
 ('P2-04','P2','Headington校历2026-27核对→女儿探亲窗口表','research','Agent','2026-11-25','','low',0,'—',''),
 ('P2-05','P2','行李/药品清单（阿联酋药品管制严格，凭处方）','todo','爱人','2026-12-20','','med',0,'处方',''),
 # P3 安家
 ('P3-01','P3','租房：Business Bay通勤半径候选表→看房→Ejari年约','research','Agent+海波','2027-01-20','P1-01','high',1,'护照/EID','签约押金红线；通勤锚点Bay Gate Tower'),
 ('P3-02','P3','DEWA水电网开户','todo','海波','2027-02-01','P1-03,P3-01','low',0,'EID/Ejari',''),
 ('P3-03','P3','银行开户（工资卡+家庭账户）','todo','海波','2027-02-01','P1-03','med',1,'EID/工资证明',''),
 ('P3-04','P3','幼儿疫苗本翻译核备（入园硬要求）','todo','爱人','2026-12-20','P0-02','med',0,'疫苗本',''),
 ('P3-05','P3','幼儿儿科诊所选定+医保落地','todo','爱人','2027-02-15','P1-04,P1-06','med',0,'—',''),
 # P4 土耳其退出
 ('P4-01','P4','退租通知（伊斯坦布尔常见1-3月通知期，先核对租约条款）','todo','爱人','2026-11-25','','high',1,'租约','最易拖过头的一项'),
 ('P4-02','P4','居留许可(kimlik)注销+税务/社保收尾','todo','海波','2027-01-10','','high',1,'kimlik',''),
 ('P4-03','P4','水电网关户+押金追回','todo','爱人','2027-01-10','P4-01','med',0,'—',''),
 ('P4-04','P4','土耳其银行账户处理','todo','海波','2027-01-10','','low',0,'—',''),
 ('P4-05','P4','车辆：卖/退租决策（土耳其车辆出口规则复杂）','research','Agent','2026-12-20','','high',1,'行驶证',''),
 ('P4-06','P4','京东安联保单核查：境外常驻是否仍有效/退保换保','research','Agent','2026-11-30','','med',0,'保单',''),
 ('P4-07','P4','幼儿在土托育退出/交接','todo','爱人','2026-12-31','','low',0,'—',''),
 # P5 女儿探访
 ('P5-01','P5','女儿护照类型确认','done','爱人','2026-11-15','','low',0,'护照','海波确认：中国普通护照'),
 ('P5-02','P5','免签规则核实：90天/180窗覆盖全部假期，无需陪同签','done','Agent','2026-11-15','','low',0,'—','来源:使馆通知2025-04-14+领事服务网+u.ae，检查2026-11-15'),
 ('P5-03','P5','每次探访checklist模板：访客医保+往返票+UK再入境证件+学校证明信','todo','Agent','2026-12-01','P2-04','low',0,'—',''),
 ('P5-04','P5','夏校>90天预案（当季家属签或60天预签，暂不启动）','todo','Agent','2027-05-01','P5-02','low',0,'—','仅当单窗超90天'),
 # P6 幼儿托育
 ('P6-01','P6','托育需求定义：通勤半径/预算/语言/理念','doing','海波+爱人','2026-11-25','','low',0,'—','通勤锚点=Bay Gate Tower'),
 ('P6-02','P6','候选nursery清单+waitlist状态+名额（先排队）','research','Agent','2026-12-10','P6-01','high',0,'—','迪拜热门园所要等位'),
 ('P6-03','P6','入园条件：年龄/疫苗/参观/费用结构','research','Agent','2026-12-15','P6-02','med',0,'疫苗本',''),
 ('P6-04','P6','家属签落地后确认入园日期','todo','爱人','2027-02-10','P1-04,P6-03','med',0,'—',''),
 # P7 车
 ('P7-01','P7','中国/土耳其驾照换迪拜驾照核查（居民用IDP的限制待核实）','research','Agent','2026-12-20','P1-03','med',0,'驾照',''),
 ('P7-02','P7','租车vs买车对比（保险/二手车/通勤定型后）','todo','Agent+海波','2027-02-10','P3-01','med',1,'—','付款红线'),
 ('P7-03','P7','Salik toll/保险/注册流程研究','research','Agent','滚动','','low',0,'—',''),
 # 治理
 ('G-01','G','每日晨报：逾期+关键路径+当日行动（07:00伊斯坦布尔）','doing','Agent','滚动','','low',0,'—','systemd timer+微信'),
 ('G-02','G','每周日反向审查：找遗漏+冲突假设','doing','Agent','滚动','','med',0,'—','周报附加段'),
 ('G-03','G','事实结论强制source+checked字段，>14天未复核标黄','done','Agent','2026-11-15','','low',0,'—','schema已内置'),
]

def main():
    if os.path.exists(DB):
        os.rename(DB, DB + '.bak-seed-' + date.today().isoformat())
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO meta(k,v) VALUES('schema_version','1')")
    for k, v in META.items():
        con.execute("INSERT INTO meta(k,v) VALUES(?,?)", (k, v))
    for t in T:
        con.execute("INSERT INTO tasks(id,phase,title,status,owner,due,depends_on,risk,human_gate,docs,notes) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], t[8], t[9], t[10]))
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    phases = con.execute("SELECT phase, COUNT(*) FROM tasks GROUP BY phase ORDER BY phase").fetchall()
    print(f'seeded {n} tasks:', phases)
    con.close()

if __name__ == '__main__':
    main()
