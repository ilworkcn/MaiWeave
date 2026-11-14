"""可编辑的角色地点图、最短路线和逐段移动。"""

import heapq
from itertools import count
from datetime import timedelta

from ..core.engine import ACTIVE, uid
from ..core.models import PlanInput


class Spatial:
    def __init__(self, engine):
        self.engine = engine
        self.check_influence = None
        store = engine.store
        if not store.all("place"):
            with store.db:
                for key, name, outdoor in [("home", "住处", False), ("bedroom", "卧室", False), ("kitchen", "厨房", False),
                                           ("street", "楼下街道", True), ("park", "公园", True), ("library", "图书馆", False)]:
                    store.put("place", {"id": key, "name": name, "outdoor": outdoor, "description": "角色世界中的虚拟地点"})
                for origin, destination, minutes in [("home", "bedroom", 1), ("home", "kitchen", 1), ("home", "street", 3),
                                                     ("street", "park", 12), ("street", "library", 18), ("park", "library", 10)]:
                    store.put("route", {"id": f"{origin}:{destination}", "origin": origin, "destination": destination, "minutes": minutes, "mode": "步行"})
                state = engine.state()
                if not state["location"]:
                    state["location"] = "住处"
                    store.put("state", state)
                elif state["location"] not in {p["name"] for p in store.all("place")}:
                    store.put("place", {"id": uid(), "name": state["location"], "outdoor": False, "description": "从已有生活状态保留的地点，请补充环境与路线"})

    def place(self, name: str) -> dict:
        place = next((p for p in self.engine.store.all("place") if name in {p["id"], p["name"]}), None)
        if place is None:
            raise ValueError(f"地点尚未登记：{name}，请先添加地点及连接路线")
        return place

    def save_place(self, name: str, outdoor: bool, description: str, place_id: str = "") -> dict:
        if not name.strip():
            raise ValueError("请填写地点名称")
        existing = next((p for p in self.engine.store.all("place") if p["name"] == name and p["id"] != place_id), None)
        if existing:
            raise ValueError("已有同名地点")
        old = self.engine.store.get("place", place_id) if place_id else None
        record = {**(old or {}), "id": place_id or uid(), "name": name, "outdoor": outdoor, "description": description}
        with self.engine.store.db:
            self.engine.store.put("place", record)
            if old:
                state = self.engine.state()
                if state["location"] == old["name"]:
                    state["location"] = name
                    self.engine.store.put("state", state)
                for category in ("plan", "routine", "habit"):
                    for item in self.engine.store.all(category):
                        if category == "plan" and item["status"] not in ACTIVE | {"paused", "blocked"}:
                            continue
                        if item.get("location") == old["name"]:
                            item["location"] = name
                        if item.get("route"):
                            for segment in [item["route"], *item["route"]["legs"]]:
                                for key in ("origin", "destination"):
                                    if segment[key] == old["name"]:
                                        segment[key] = name
                        self.engine.store.put(category, item)
        return record

    def save_route(self, origin: str, destination: str, minutes: int, mode: str) -> dict:
        a, b = self.place(origin), self.place(destination)
        if a["id"] == b["id"] or minutes < 1:
            raise ValueError("路线需连接两个不同地点，耗时至少一分钟")
        key = ":".join(sorted([a["id"], b["id"]]))
        record = {"id": key, "origin": a["id"], "destination": b["id"], "minutes": minutes, "mode": mode}
        with self.engine.store.db:
            for old in self.engine.store.all("route"):
                if {old["origin"], old["destination"]} == {a["id"], b["id"]}:
                    self.engine.store.delete("route", old["id"])
            self.engine.store.put("route", record)
        return record

    def route(self, origin: str, destination: str) -> dict:
        start, end = self.place(origin), self.place(destination)
        graph = {}
        for edge in self.engine.store.all("route"):
            for a, b in [(edge["origin"], edge["destination"]), (edge["destination"], edge["origin"])]:
                graph.setdefault(a, []).append((b, edge["minutes"], edge["mode"]))
        serial = count()
        queue, visited = [(0, next(serial), start["id"], [])], set()
        while queue:
            minutes, _, node, legs = heapq.heappop(queue)
            if node in visited:
                continue
            visited.add(node)
            if node == end["id"]:
                return {"origin": start["name"], "destination": end["name"], "minutes": minutes, "legs": legs}
            for target, duration, mode in graph.get(node, []):
                if target not in visited:
                    leg = {"origin": self.place(node)["name"], "destination": self.place(target)["name"], "minutes": duration, "mode": mode}
                    heapq.heappush(queue, (minutes + duration, next(serial), target, legs + [leg]))
        raise ValueError("地点之间没有连通路线，请先补充路线")

    def projected_location(self, at, now, excluded=()) -> str:
        previous = [p for p in self.engine.store.all("plan") if p["id"] not in excluded and p["status"] in ACTIVE and self.engine.time(p["end"]) <= at and self.engine.time(p["end"]) >= now and p["location"]]
        if previous:
            return max(previous, key=lambda p: self.engine.time(p["end"]))["location"]
        return self.engine.state()["location"]

    def reconcile(self, now):
        """沿整条未完成时间线重算后续路线，固定安排冲突进入待调整。"""
        engine, store = self.engine, self.engine.store
        plans = store.all("plan")
        origin, available = engine.state()["location"], now
        used = set()
        pending = [p for p in plans if not p.get("activity_id") and p["status"] in ACTIVE | {"blocked"}]
        moving = next((p for p in plans if p.get("activity_id") and p["status"] == "active"), None)
        if moving:
            origin, available = moving["location"], engine.time(moving["end"])
            used.add(moving["id"])
        while pending:
            pending.sort(key=lambda p: engine.time(p["start"]))
            plan = pending.pop(0)
            if plan["status"] == "active":
                origin = plan["location"] or origin
                available = max(available, engine.time(plan["end"]))
                if plan.get("travel_id"):
                    used.add(plan["travel_id"])
                continue
            start, end = engine.time(plan["start"]), engine.time(plan["end"])
            duration = end - start
            try:
                route = self.route(origin, plan["location"]) if plan["location"] else {"minutes": 0, "legs": [], "origin": origin, "destination": origin}
                minutes = route["minutes"] if plan["kind"] != "travel" else 0
                earliest = available + timedelta(minutes=minutes)
                proposed = max(start, earliest)
                deadline = engine.time(plan["sleep_deadline"]) if plan.get("sleep_deadline") else None
                if deadline:
                    duration = min(duration, deadline - proposed)
                    if duration.total_seconds() <= 0:
                        engine.transition(plan["id"], "cancel", plan["session_id"], now)
                        continue
                if plan.get("flexible", plan["autonomous"]):
                    fixed = next((p for p in pending if not p.get("flexible", p["autonomous"])), None)
                    if fixed:
                        onward = self.route(plan["location"] or origin, fixed["location"])["minutes"] if fixed["location"] else 0
                        if proposed + duration + timedelta(minutes=onward) > engine.time(fixed["start"]):
                            if deadline:
                                duration = engine.time(fixed["start"]) - timedelta(minutes=onward) - proposed
                                if duration.total_seconds() <= 0:
                                    engine.transition(plan["id"], "cancel", plan["session_id"], now)
                                    continue
                            else:
                                plan.update(start=engine.time(fixed["end"]).isoformat(), end=(engine.time(fixed["end"]) + duration).isoformat())
                                pending.append(plan)
                                continue
                    if proposed != start:
                        plan["changes"].append({"at": now.isoformat(), "action": "自动顺延", "before": plan["start"], "reason": "前序活动与出行耗时变化"})
                        start, end = proposed, proposed + duration
                elif start < earliest and not (not minutes and start <= now < end and available == now):
                    raise ValueError(f"前序安排后最早 {earliest.isoformat()} 才能到达，需调整固定安排")
                if plan["kind"] == "travel":
                    if duration.total_seconds() < route["minutes"] * 60:
                        raise ValueError(f"路线改变后需要 {route['minutes']} 分钟，请调整出行结束时间")
                    plan["route"] = route
                if deadline:
                    end = start + duration
                plan.update(start=start.isoformat(), end=end.isoformat(), status="scheduled")
                plan.pop("blocking_reason", None)
                if minutes:
                    departure = start - timedelta(minutes=minutes)
                    old_trip = store.find("plan", plan.get("travel_id", ""))
                    if old_trip and old_trip["status"] == "active":
                        used.add(old_trip["id"])
                    else:
                        trip = {**PlanInput(title=f"前往{route['destination']}", start=departure.isoformat(), end=start.isoformat(),
                                           kind="travel", location=route["destination"], motivation=f"为{plan['title']}出行").model_dump(),
                                "id": old_trip["id"] if old_trip and old_trip["status"] == "scheduled" else uid(),
                                "session_id": plan["session_id"], "status": "scheduled", "autonomous": plan["autonomous"],
                                "elapsed_seconds": 0, "created_at": now.isoformat(), "changes": [], "route": route, "activity_id": plan["id"]}
                        plan["travel_id"] = trip["id"]
                        used.add(trip["id"])
                        with store.db:
                            store.put("plan", trip)
                elif plan.get("travel_id") not in used:
                    plan.pop("travel_id", None)
                origin, available = plan["location"] or origin, max(available, end)
                with store.db:
                    store.delete("schedule_issue", plan["id"])
            except ValueError as error:
                plan["status"] = "blocked"
                plan["blocking_reason"] = str(error)
                with store.db:
                    store.put("schedule_issue", {"id": plan["id"], "session_id": plan["session_id"], "title": plan["title"],
                                                 "reason": str(error), "at": now.isoformat()})
            with store.db:
                store.put("plan", plan)
        with store.db:
            for trip in store.all("plan"):
                if trip.get("activity_id") and trip["status"] == "scheduled" and trip["id"] not in used:
                    trip["status"] = "cancelled"
                    store.put("plan", trip)
            for issue in store.all("schedule_issue"):
                if store.get("plan", issue["id"])["status"] != "blocked":
                    store.delete("schedule_issue", issue["id"])

    def transition(self, plan_id, action, session, now):
        plan = self.engine.owned("plan", plan_id, session)
        if action == "resume" and plan["status"] == "paused" and plan["kind"] != "travel" and plan["location"]:
            route = self.route(self.engine.state()["location"], plan["location"])
            arrival = now + timedelta(minutes=route["minutes"])
            end = arrival + timedelta(seconds=plan["remaining_seconds"])
            self.engine._check_interval(now, end, {plan_id, plan.get("travel_id", "")})
            plan.update(start=arrival.isoformat(), end=end.isoformat(), status="scheduled")
            plan["changes"].append({"at": now.isoformat(), "action": "恢复活动并重新安排出行"})
            with self.engine.store.db:
                self.engine.store.put("plan", plan)
            result = plan
        else:
            result = self.engine.transition(plan_id, action, session, now)
        if action == "cancel" and plan.get("sleep_deadline"):
            with self.engine.store.db:
                self.engine.store.put("runtime", {"id": "sleep_skip", "until": plan["sleep_deadline"]})
        if action == "cancel" and plan["kind"] == "sleep" and self.engine.time(plan["start"]) <= now:
            # 手动结束当前睡眠时，同时结束这次唤醒留下的续睡安排。
            with self.engine.store.db:
                self.engine.store.delete("runtime", "awakening")
        self.reconcile(now)
        return self.engine.store.get("plan", result["id"])

    def make_room(self, start, end, now):
        """明确的新安排占用时间时，让自主活动让出该时间段。"""
        conflicts = [p for p in self.engine.store.all("plan") if p["status"] in ACTIVE and not p.get("activity_id")
                     and self.engine.time(p["start"]) < end and self.engine.time(p["end"]) > start]
        if any(not p["autonomous"] or p["participants"] for p in conflicts):
            raise ValueError("这段时间已有固定安排，请先调整原安排")
        with self.engine.store.db:
            for plan in conflicts:
                duration = self.engine.time(plan["end"]) - max(now, self.engine.time(plan["start"]))
                plan["changes"].append({"at": now.isoformat(), "action": "为新安排让出时间", "before": plan["start"]})
                plan.update(start=end.isoformat(), end=(end + duration).isoformat(), status="scheduled")
                self.engine.store.put("plan", plan)
                if plan.get("travel_id"):
                    trip = self.engine.store.get("plan", plan["travel_id"])
                    if trip["status"] == "scheduled":
                        trip["status"] = "cancelled"
                        self.engine.store.put("plan", trip)

    def move(self, destination: str, session: str, now) -> dict:
        self.engine.advance(now)
        route = self.route(self.engine.state()["location"], destination)
        if route["minutes"] == 0:
            raise ValueError("已经在这个地点")
        plan = self.engine.create_plan(PlanInput(title=f"前往{route['destination']}", kind="travel", start=now.isoformat(),
                                               end=(now + timedelta(minutes=route["minutes"])).isoformat(), location=route["destination"],
                                               motivation="按路线移动"), session, now)
        plan["route"] = route
        with self.engine.store.db:
            self.engine.store.put("plan", plan)
        self.reconcile(now)
        return plan

    def change(self, plan_id, patch, session, now):
        old = self.engine.owned("plan", plan_id, session)
        changes = patch.model_dump(exclude_unset=True)
        if not {"start", "location"} & changes.keys() or old["kind"] == "travel":
            self.engine.change_plan(plan_id, patch, session, now)
            self.reconcile(now)
            return self.engine.store.get("plan", plan_id)
        target = changes.get("location", old["location"])
        if not target:
            target = self.engine.state()["location"]
        start = self.engine.time(changes.get("start", old["start"]))
        excluded = {plan_id, old.get("travel_id", "")}
        origin = self.projected_location(start, now, excluded)
        route = self.route(origin, target)
        departure = start - timedelta(minutes=route["minutes"])
        if route["minutes"] and departure < now:
            raise ValueError(f"改期后没有足够出行时间，需要提前 {route['minutes']} 分钟出发")
        if route["minutes"]:
            self.engine._check_interval(departure, start, excluded)
        updated = self.engine.change_plan(plan_id, patch, session, now)
        if old.get("travel_id"):
            travel = self.engine.store.get("plan", old["travel_id"])
            if travel["status"] in ACTIVE:
                self.engine.transition(travel["id"], "cancel", session, now)
        updated.pop("travel_id", None)
        if route["minutes"]:
            travel = self.engine.create_plan(PlanInput(title=f"前往{route['destination']}", kind="travel", start=departure.isoformat(),
                                                      end=start.isoformat(), location=route["destination"], motivation=f"为{updated['title']}出行"), session, now)
            travel.update(route=route, activity_id=plan_id)
            updated["travel_id"] = travel["id"]
            with self.engine.store.db:
                self.engine.store.put("plan", travel)
        with self.engine.store.db:
            self.engine.store.put("plan", updated)
        self.reconcile(now)
        return self.engine.store.get("plan", plan_id)

    def schedule(self, value: PlanInput, session: str, now, autonomous: bool = False, user_id=None, sleep_deadline: str = "") -> dict:
        """地点变化时，在活动前显式安排出行时间。"""
        self.engine.validate_plan(value, session, now)
        if user_id is not None:
            start, end = self.engine.time(value.start), self.engine.time(value.end)
            if value.location and value.kind != "travel":
                origin = self.projected_location(start, now)
                start -= timedelta(minutes=self.route(origin, value.location)["minutes"])
            overlaps = [p for p in self.engine.store.all("plan") if p["status"] in ACTIVE
                        and self.engine.time(p["start"]) < end and self.engine.time(p["end"]) > start]
            self.check_influence(session, user_id, overlaps)
        if value.kind == "travel":
            route = self.route(self.projected_location(self.engine.time(value.start), now), value.location)
            if (self.engine.time(value.end) - self.engine.time(value.start)).total_seconds() < route["minutes"] * 60:
                raise ValueError(f"出行时间不足，这条路线需要 {route['minutes']} 分钟")
            if not autonomous:
                self.make_room(self.engine.time(value.start), self.engine.time(value.end), now)
            plan = self.engine.create_plan(value.model_copy(update={"location": route["destination"]}), session, now, autonomous)
            plan["route"] = route
            with self.engine.store.db:
                self.engine.store.put("plan", plan)
            self.reconcile(now)
            return self.engine.store.get("plan", plan["id"])
        if not value.location:
            if not autonomous:
                self.make_room(self.engine.time(value.start), self.engine.time(value.end), now)
            plan = self.engine.create_plan(value, session, now, autonomous)
            self.reconcile(now)
            return self.engine.store.get("plan", plan["id"])
        target = self.place(value.location)
        start = self.engine.time(value.start)
        origin = self.projected_location(start, now)
        route = self.route(origin, target["id"])
        travel_start = start - timedelta(minutes=route["minutes"])
        if route["minutes"]:
            if travel_start < now:
                earliest = now + timedelta(minutes=route["minutes"])
                raise ValueError(f"从{origin}到{target['name']}需要{route['minutes']}分钟，最早{earliest.isoformat()}开始")
        if not autonomous:
            self.make_room(travel_start, self.engine.time(value.end), now)
        if route["minutes"]:
            self.engine._check_interval(travel_start, start)
        value = value.model_copy(update={"location": target["name"]})
        plan = self.engine.create_plan(value, session, now, autonomous)
        if sleep_deadline:
            plan["sleep_deadline"] = sleep_deadline
            with self.engine.store.db:
                self.engine.store.put("plan", plan)
        if route["minutes"]:
            travel = self.engine.create_plan(PlanInput(title=f"前往{target['name']}", kind="travel", start=travel_start.isoformat(),
                                                      end=start.isoformat(), location=target["name"], motivation=f"为{value.title}出行"), session, now, autonomous)
            travel.update(route=route, activity_id=plan["id"])
            plan["travel_id"] = travel["id"]
            with self.engine.store.db:
                self.engine.store.put("plan", travel)
                self.engine.store.put("plan", plan)
        self.reconcile(now)
        return self.engine.store.get("plan", plan["id"])
