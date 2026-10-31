"""可编辑的角色地点图、最短路线和逐段移动。"""

import heapq
from itertools import count
from datetime import timedelta

from .engine import ACTIVE, uid
from .models import PlanInput


class Spatial:
    def __init__(self, engine):
        self.engine = engine
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
            raise ValueError("地点名称不能为空")
        existing = next((p for p in self.engine.store.all("place") if p["name"] == name and p["id"] != place_id), None)
        if existing:
            raise ValueError("已有同名地点")
        old = self.engine.store.get("place", place_id) if place_id else None
        record = {"id": place_id or uid(), "name": name, "outdoor": outdoor, "description": description}
        with self.engine.store.db:
            self.engine.store.put("place", record)
            if old:
                state = self.engine.state()
                if state["location"] == old["name"]:
                    state["location"] = name
                    self.engine.store.put("state", state)
                for category in ("plan", "routine", "habit"):
                    for item in self.engine.store.all(category):
                        if category == "plan" and item["status"] not in ACTIVE | {"paused"}:
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
        return plan

    def change(self, plan_id, patch, session, now):
        old = self.engine.owned("plan", plan_id, session)
        changes = patch.model_dump(exclude_unset=True)
        if not {"start", "location"} & changes.keys() or old["kind"] == "travel":
            return self.engine.change_plan(plan_id, patch, session, now)
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
        return updated

    def schedule(self, value: PlanInput, session: str, now, autonomous: bool = False) -> dict:
        """地点变化时，在活动前显式安排出行时间。"""
        if value.kind == "travel":
            route = self.route(self.projected_location(self.engine.time(value.start), now), value.location)
            if (self.engine.time(value.end) - self.engine.time(value.start)).total_seconds() < route["minutes"] * 60:
                raise ValueError(f"出行时间不足，这条路线需要 {route['minutes']} 分钟")
            plan = self.engine.create_plan(value.model_copy(update={"location": route["destination"]}), session, now, autonomous)
            plan["route"] = route
            with self.engine.store.db:
                self.engine.store.put("plan", plan)
            return plan
        if not value.location:
            return self.engine.create_plan(value, session, now, autonomous)
        target = self.place(value.location)
        start = self.engine.time(value.start)
        origin = self.projected_location(start, now)
        route = self.route(origin, target["id"])
        travel_start = start - timedelta(minutes=route["minutes"])
        if route["minutes"]:
            if travel_start < now:
                earliest = now + timedelta(minutes=route["minutes"])
                raise ValueError(f"从{origin}到{target['name']}需要{route['minutes']}分钟，最早{earliest.isoformat()}开始")
            self.engine._check_interval(travel_start, start)
        value = value.model_copy(update={"location": target["name"]})
        plan = self.engine.create_plan(value, session, now, autonomous)
        if route["minutes"]:
            travel = self.engine.create_plan(PlanInput(title=f"前往{target['name']}", kind="travel", start=travel_start.isoformat(),
                                                      end=start.isoformat(), location=target["name"], motivation=f"为{value.title}出行"), session, now, autonomous)
            travel.update(route=route, activity_id=plan["id"])
            plan["travel_id"] = travel["id"]
            with self.engine.store.db:
                self.engine.store.put("plan", travel)
                self.engine.store.put("plan", plan)
        return plan


def travel_position(plan: dict, at, engine) -> dict:
    route = plan["route"]
    elapsed = plan["elapsed_seconds"] / 60
    for leg in route["legs"]:
        if elapsed < leg["minutes"]:
            return {"location": leg["origin"], "next": leg["destination"], "mode": leg["mode"],
                    "progress": round(elapsed / leg["minutes"] * 100), "destination": route["destination"]}
        elapsed -= leg["minutes"]
    return {"location": route["destination"], "next": "", "mode": "已到达", "progress": 100, "destination": route["destination"]}
