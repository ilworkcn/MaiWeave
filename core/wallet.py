"""角色共用的虚拟钱包、活动收支和私聊借款。"""

from calendar import monthrange
from datetime import date, timedelta
from uuid import uuid4


OPENING_BALANCE = 1500
SHIFT_HOURLY_PAY = 30
MONTHLY_BILLS = {"rent": "房租", "utilities": "生活杂费"}


def rent_date(month, day):
    return month.replace(day=min(day, monthrange(month.year, month.month)[1]))


def next_month(month):
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1)


class Wallet:
    def __init__(self, store):
        self.store = store
        self.currency_name = "虚拟元"

    def account(self):
        return self.store.find("runtime", "wallet") or {"id": "wallet", "opening": OPENING_BALANCE, "balance": OPENING_BALANCE}

    def pantry(self):
        return self.store.find("runtime", "pantry") or {"id": "pantry", "raw": 3, "ready": 0}

    def meal_options(self, pantry):
        options = []
        if pantry["ready"] > 0:
            options.append("回家吃留餐（meal，food=吃留餐）")
        if pantry["raw"] > 0:
            options.append("在家现做现吃（meal），或先备料、做好留餐（cook）")
        if self.account()["balance"] > 0:
            options.append("按余额和路程选外食（meal，food=外食，cost填餐费），或买菜（groceries填买够几餐，cost填菜钱）")
        options.append("已有请客或供餐依据时可选受招待（meal）")
        return "；".join(options)

    def food_action(self, choice):
        pantry = self.pantry()
        food = choice.food
        if choice.groceries and not choice.cost:
            raise ValueError("买菜请填写这次实际花费")
        if choice.kind == "meal" and choice.continuation_id and pantry["ready"] and not choice.cost:
            memory = self.store.find("activity_memory", choice.continuation_id) or {}
            event = self.store.find("experience", memory.get("experience_id", "")) or {}
            prepared = self.store.find("plan", event.get("plan_id", "")) or {}
            if prepared.get("food") == "做好留餐":
                food = "吃留餐"
        if not food and choice.kind == "meal":
            if choice.cost and not choice.groceries:
                food = "外食"
            else:
                food = "吃留餐" if pantry["ready"] else "现做现吃"
        if not food and choice.kind == "cook":
            food = "做好留餐"
        if (food in {"做好留餐", "备料"} and choice.kind != "cook"
                or food in {"现做现吃", "吃留餐", "外食", "受招待"} and choice.kind != "meal"):
            raise ValueError("请按实际活动填写做饭或用餐类型")
        if food in {"备料", "做好留餐", "现做现吃"} and pantry["raw"] + choice.groceries < 1:
            raise ValueError("家里食材已用完。" + self.meal_options(pantry))
        if food == "吃留餐" and not pantry["ready"]:
            raise ValueError("目前没有已经做好的留餐。" + self.meal_options(pantry))
        if food == "外食" and not choice.cost:
            raise ValueError("在外购买成品餐请填写餐费")
        return food

    def set_opening(self, amount):
        if amount < 0 or self.store.count("payment"):
            raise ValueError("起始生活费可在第一笔收支前设置，金额须为非负整数")
        with self.store.db:
            return self.store.put("runtime", {"id": "wallet", "balance": amount, "opening": amount})

    def configure_rent(self, amount, day, now, utilities=0):
        for key, price in (("rent", amount), ("utilities", utilities)):
            self._configure_bill(key, price, day, now)

    def _configure_bill(self, key, amount, day, now):
        rent = self.store.find("runtime", key)
        if not rent and not amount:
            return
        if rent and (rent["amount"], rent["day"]) == (amount, day):
            return
        today = now.date()
        with self.store.db:
            if rent:
                self._accrue_bills(rent, today)
            else:
                rent = {"id": key, "pending": [], "last_month": ""}
            due = rent_date(today, day)
            if due < today or due.isoformat()[:7] == rent["last_month"]:
                due = rent_date(next_month(today), day)
            rent.update(amount=amount, day=day, next=due.isoformat() if amount else "")
            self.store.put("runtime", rent)

    def _accrue_bills(self, rent, today):
        while rent["next"] and rent["next"] <= today.isoformat():
            due = date.fromisoformat(rent["next"])
            month = rent["next"][:7]
            rent["pending"].append({"month": month, "amount": rent["amount"]})
            rent["last_month"] = month
            rent["next"] = rent_date(next_month(due), rent["day"]).isoformat()

    def _settle_monthly_bills(self, now):
        for key, label in MONTHLY_BILLS.items():
            self._settle_bill(key, label, now)

    def _settle_bill(self, key, label, now):
        rent = self.store.find("runtime", key)
        if not rent or not (rent["pending"] or rent["next"] and rent["next"] <= now.date().isoformat()):
            return
        previous_due = rent["next"]
        previous_count = len(rent["pending"])
        self._accrue_bills(rent, now.date())
        while rent["pending"]:
            bill = rent["pending"][0]
            purpose = label + ":" + bill["month"]
            title = bill["month"] + " " + label
            requests = [r for r in self.store.all("money_request") if r["purpose"] == purpose]
            if self.account()["balance"] < bill["amount"]:
                if not requests:
                    self._need(title, bill["amount"], "", now, purpose)
                break
            self._post(purpose, -bill["amount"], title, now)
            for row in requests:
                if row["status"] in {"待询问", "答应稍后给"}:
                    row["status"] = "已够用"
                    row["updated_at"] = now.isoformat()
                self.store.put("money_request", row)
            rent["pending"].pop(0)
        if previous_due != rent["next"] or previous_count != len(rent["pending"]):
            self.store.put("runtime", rent)

    def rent_note(self):
        return "；".join(n for key, label in MONTHLY_BILLS.items() if (n := self._bill_note(key, label)))

    def _bill_note(self, key, label):
        rent = self.store.find("runtime", key)
        if rent and rent["pending"]:
            bill = rent["pending"][0]
            gap = max(0, bill["amount"] - self.account()["balance"])
            text = f"{bill['month']} {label}待缴 · {bill['amount']} {self.currency_name}"
            if gap:
                text += f" · 还差 {gap} {self.currency_name}"
            if len(rent["pending"]) > 1:
                text += f" · 共 {len(rent['pending'])} 期待缴"
            return text
        if rent and rent["amount"]:
            title = "下次交租" if key == "rent" else "下次缴生活杂费"
            return f"{title} {rent['next']} · {rent['amount']} {self.currency_name}"
        return ""

    def spending_room(self, now, completed):
        """用最近七个完整日的餐费、菜钱和即将到期的账单估算余款。"""
        today = now.date()
        food_by_day = {}
        for p in completed:
            day = date.fromisoformat(p["end"][:10])
            if 1 <= (today - day).days <= 7 and (p["kind"] == "meal" or p.get("groceries")):
                food_by_day[day] = food_by_day.get(day, 0) + p.get("cost", 0)
        meals = sum(food_by_day.values()) + 40 * (7 - len(food_by_day))
        bills = 0
        for key in MONTHLY_BILLS:
            bill = self.store.find("runtime", key)
            if bill:
                bills += sum(r["amount"] for r in bill["pending"])
                if bill["next"] and date.fromisoformat(bill["next"]) <= today + timedelta(days=7):
                    bills += bill["amount"]
        return {"近期账单": bills, "一周饭钱估计": meals, "预留后余款": self.account()["balance"] - bills - meals}

    def _post(self, key, amount, title, now, session="", user=""):
        if self.store.find("payment", key):
            return False
        account = self.account()
        account["balance"] += amount
        self.store.put("runtime", account)
        self.store.put("payment", {"id": key, "amount": amount, "balance": account["balance"],
                                   "title": title, "at": now.isoformat(), "session": session, "user": user})
        return True

    def recent_payments(self, session, user, limit=8):
        """按时间取公共生活和当前私聊可见的流水。"""
        result = []
        page = 1
        while len(result) < limit:
            batch = self.store.page("payment", page=page, size=20, time_field="at")
            for row in batch["items"]:
                kind, _, source = row["id"].partition(":")
                if kind in {"来款", "还款"} and not (session and user):
                    continue
                if "session" in row:
                    if row["session"] not in ("", session) or row.get("user") not in ("", user):
                        continue
                elif kind in {"来款", "还款"}:
                    key = source.partition(":")[2] if kind == "还款" else source
                    loan = self.store.find("money_request", key)
                    if not loan or (loan["session"], loan["user"]) != (session, user):
                        continue
                elif kind != "房租":
                    plan = self.store.find("plan", source)
                    if not plan or plan["session_id"] not in ("", session):
                        continue
                result.append({k: row[k] for k in ("title", "amount", "at")})
                if len(result) == limit:
                    break
            if page >= batch["pages"]:
                break
            page += 1
        return result

    def pay_fare(self, plan, now):
        fare = sum(leg.get("fare", 0) for leg in plan["route"]["legs"])
        if not fare:
            return ""
        self._settle_monthly_bills(now)
        balance = self.account()["balance"]
        if balance < fare:
            return f"车费需要 {fare} {self.currency_name}，存款只有 {balance} {self.currency_name}"
        self._post("车费:" + plan["id"], -fare, plan["title"], now, plan.get("session_id", ""))
        return ""

    def settle_food(self, plan):
        """把本次实际买入、做成或吃掉的餐数记入冰箱。"""
        food, bought = plan.get("food", ""), plan.get("groceries", 0)
        if bought or food in {"做好留餐", "现做现吃", "吃留餐"}:
            experience = self.store.get("experience", plan["experience_id"])
            if "家中余粮" not in experience["details"]:
                pantry = self.pantry()
                pantry["raw"] = max(0, pantry["raw"] + bought - int(food in {"做好留餐", "现做现吃"}))
                pantry["ready"] += int(food == "做好留餐") - int(food == "吃留餐")
                self.store.put("runtime", pantry)
                experience["details"]["家中余粮"] = f"食材{pantry['raw']}餐，已做好{pantry['ready']}餐"
                self.store.put("experience", experience)

    def settle(self, plan, now):
        """随完成记录在同一个事务里记账。"""
        self.settle_food(plan)
        fare = sum(leg.get("fare", 0) for leg in (plan.get("route") or {}).get("legs", []))
        cost = plan.get("cost", 0) + fare
        if cost or plan.get("pay"):
            experience = self.store.get("experience", plan["experience_id"])
            if cost:
                experience["details"]["生活支出"] = f"{cost}{self.currency_name}"
            if plan.get("pay"):
                experience["details"]["工作报酬"] = f"{plan['pay']}{self.currency_name}"
            self.store.put("experience", experience)
        if plan.get("purchase_request"):
            request = self.store.get("money_request", plan["purchase_request"])
            request["purchased_at"] = now.isoformat()
            self.store.put("money_request", request)
        if fare and self.store.find("payment", "车费:" + plan["id"]):
            cost -= fare
        if cost:
            self._post("消费:" + plan["id"], -cost, plan["title"], now, plan.get("session_id", ""))
        amount = plan.get("pay", 0)
        if amount and self._post("工钱:" + plan["id"], amount, plan["title"], now, plan.get("session_id", "")):
            available = min(amount, self.account()["balance"])
            debts = [r for r in self.store.all("money_request") if r["status"] == "借入"]
            def received_at(row):
                payment = self.store.find("payment", "来款:" + row["id"])
                return payment["at"] if payment else row["date"]
            for request in sorted(debts, key=received_at):
                if available <= 0:
                    break
                paid = min(request["owed"], available)
                self._post("还款:" + plan["id"] + ":" + request["id"], -paid, "归还借款 · " + request["title"], now, request["session"], request["user"])
                request["owed"] -= paid
                request["updated_at"] = now.isoformat()
                available -= paid
                if not request["owed"]:
                    request["status"] = "已还清"
                self.store.put("money_request", request)
        if amount:
            self._settle_monthly_bills(now)

    def planning_note(self, now):
        rows = [r for r in self.store.all("money_request") if r["date"] == now.date().isoformat() and not r["source_session"] and not r.get("purchased_at") and not r["purpose"].startswith(("房租:", "生活杂费:", "通勤:"))]
        if not rows:
            return "暂无"
        row = rows[-1]
        return f"{row['title']}预计{row['price']}{self.currency_name}，{row['status']}。决定去办时沿用原活动标题，手头紧可安排免费的活动。"

    def purchase_for(self, session, title, continuation_id):
        return next((r["id"] for r in reversed(self.store.all("money_request"))
                     if r["source_session"] == session and r["purpose"] == (continuation_id or title)
                     and not r.get("purchased_at") and r["status"] in {"借入", "赠予", "已够用", "已还清"}), "")

    def need(self, title, price, session, now, continuation_id=""):
        with self.store.db:
            self._need(title, price, session, now, continuation_id)

    def _need(self, title, price, session, now, continuation_id):
        balance = self.account()["balance"]
        if price <= balance:
            return
        rows = self.store.all("money_request")
        today = now.date().isoformat()
        purpose = continuation_id or title
        if any(r["status"] == "答应稍后给" or r["date"] == today or
               (r["source_session"] == session and r["purpose"] == purpose and r["status"] in {"已过期", "已婉拒", "待询问"})
               for r in rows):
            return
        self.store.put("money_request", {"id": uuid4().hex[:12], "title": title, "price": price,
            "date": today, "purpose": purpose, "source_session": session, "session": "", "user": "", "status": "待询问",
            "amount": price - balance, "owed": 0})

    def refresh(self, now):
        with self.store.db:
            self._settle_monthly_bills(now)
            balance = self.account()["balance"]
            for row in self.store.all("money_request"):
                if row["status"] in {"待询问", "答应稍后给"}:
                    if row["price"] <= balance:
                        row["status"] = "已够用"
                    elif row["status"] == "待询问" and row["date"] < now.date().isoformat():
                        row["status"] = "已过期"
                    else:
                        continue
                    row["updated_at"] = now.isoformat()
                    self.store.put("money_request", row)

    def pending(self, now):
        return [r for r in self.store.all("money_request") if r["date"] == now.date().isoformat()
                and r["status"] == "待询问" and r["price"] > self.account()["balance"]]

    def notice(self, request, session, user):
        if request["source_session"] not in ("", session) or request["session"] not in ("", session):
            return None
        gap = request["price"] - self.account()["balance"]
        if request["session"]:
            gap = min(gap, request["amount"])
        if gap <= 0:
            return None
        with self.store.db:
            request.update(session=session, user=user, amount=gap)
            self.store.put("money_request", request)
        return ("wallet:" + request["id"], f"{request['title']}还差{gap}{self.currency_name}，能借我一下吗？下次领了工钱就还你。", None)

    def current_notice(self, receipt, now):
        key = receipt.get("record_id", "")
        if key.startswith("installment:"):
            _, remaining, loan_id = key.split(":", 2)
            loan = self.store.find("money_request", loan_id)
            return bool(loan and loan["session"] == receipt["session"] and str(loan["owed"]) == remaining)
        if not key.startswith("wallet:"):
            return True
        request = self.store.get("money_request", key.split(":", 1)[1])
        return (request["session"] == receipt["session"] and request["status"] == "待询问"
                and request["date"] == now.date().isoformat()
                and request["amount"] <= request["price"] - self.account()["balance"])

    def commute_requests(self, session, user, now):
        balance = self.account()["balance"]
        offers = []
        for plan in self.store.all("plan"):
            trip = plan.get("commute")
            if not trip or plan["status"] != "blocked" or plan["session_id"] not in ("", session):
                continue
            key = "通勤:" + plan["id"]
            gap = trip["fare"] - balance
            if gap <= 0 or plan["end"] < now.isoformat() or self.store.find("money_request", key):
                continue
            offers.append({"id": key, "title": plan["title"] + "的车费", "price": trip["fare"], "amount": gap,
                           "purpose": key, "source_session": plan["session_id"], "session": session, "user": user,
                           "date": now.date().isoformat(), "status": "待询问", "owed": 0, "departure": trip["departure"]})
        return sorted(offers, key=lambda row: row["departure"])

    def respond(self, key, session, user, action, now, amount=0):
        row = self.store.find("money_request", key)
        if row is None and key.startswith("通勤:"):
            row = next((r for r in self.commute_requests(session, user, now) if r["id"] == key), None)
        if row is None:
            raise ValueError("当前私聊没有这笔待处理的借款")
        if (row["session"], row["user"]) != (session, user):
            raise ValueError("请选择当前私聊的借款")
        if row["status"] not in {"待询问", "答应稍后给"}:
            return row
        if key.startswith("通勤:"):
            if action not in {"lend", "give"}:
                raise ValueError("等对方确认借出或赠予车费时再入账")
            plan = self.store.get("plan", key.split(":", 1)[1])
            commute = plan.get("commute")
            if plan["status"] != "blocked" or not commute or now.isoformat() > commute["departure"]:
                raise ValueError("这班车费已经用不上了，先调整排班或路线")
            row.update(date=now.date().isoformat(), price=commute["fare"])
            if action in {"lend", "give"} and amount and amount != row["price"] - self.account()["balance"]:
                raise ValueError("请按当前班次所差的车费确认金额")
        if row["price"] <= self.account()["balance"] or (row["date"] != now.date().isoformat() and row["status"] == "待询问"):
            raise ValueError("这笔生活费已经够用或申请已过期")
        status = {"promise": "答应稍后给", "lend": "借入", "give": "赠予", "decline": "已婉拒"}[action]
        with self.store.db:
            if action in {"lend", "give"}:
                limit = min(row["amount"], row["price"] - self.account()["balance"])
                if amount < 0 or amount > limit:
                    raise ValueError(f"这笔生活费还差{limit}{self.currency_name}，请核对金额")
                row["amount"] = amount or limit
                self._post("来款:" + key, row["amount"], status + " · " + row["title"], now, session, user)
            row.update(status=status, owed=row["amount"] if action == "lend" else 0, updated_at=now.isoformat())
            self.store.put("money_request", row)
            if action in {"lend", "give"}:
                self._settle_monthly_bills(now)
            return row
