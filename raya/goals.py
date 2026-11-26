from uuid import uuid4

from .core import digest, require, text_field


def goal(profile, ident):
    item = profile.get("goals", ident)
    require(item, "unknown goal")
    return item


def change(profile, command, args):
    if command == "goal-create":
        ident = uuid4().hex
        item = dict(
            id=ident, title=text_field(args["title"], "title"), state="active", tasks={}
        )
    else:
        ident = args["goal"]
        item = goal(profile, ident)
        tasks = item["tasks"]
        if command == "goal-state":
            state = args["state"]
            require(
                item["state"] != "cancelled"
                and state in {"active", "paused", "cancelled"},
                "invalid transition",
            )
            item["state"] = state
        else:
            require(item["state"] == "active", "goal inactive")
            if command == "task-add":
                deps = args.get("depends", [])
                require(
                    isinstance(deps, list) and all(dep in tasks for dep in deps),
                    "unknown dependency",
                )
                task_id = uuid4().hex
                tasks[task_id] = dict(
                    title=text_field(args["title"], "title"),
                    depends=deps,
                    state="pending",
                )
                item["latest_task"] = task_id
            else:
                task_id = args["task"]
                require(task_id in tasks, "unknown task")
                task = tasks[task_id]
                if command == "handoff":
                    require(
                        task["state"] in {"pending", "dispatched"}, "handoff closed"
                    )
                    require(
                        all(
                            tasks[dep]["state"] == "verified" for dep in task["depends"]
                        ),
                        "dependencies pending",
                    )
                    task.setdefault(
                        "handoff",
                        dict(
                            id=uuid4().hex,
                            goal=ident,
                            task=task_id,
                            scope=task["title"],
                        ),
                    )
                    task["state"] = "dispatched"
                elif command == "collect":
                    require(
                        args["handoff"] == task.get("handoff")
                        and task["state"] in {"dispatched", "received", "verified"},
                        "handoff mismatch",
                    )
                    evidence = text_field(args["evidence"], "evidence", 65536)
                    require(
                        digest(evidence) == args["digest"], "evidence digest mismatch"
                    )
                    require(
                        task.get("digest", args["digest"]) == args["digest"],
                        "conflicting delivery",
                    )
                    task.update(evidence=evidence, digest=args["digest"])
                    if task["state"] == "dispatched":
                        task["state"] = "received"
                elif command == "review":
                    require(
                        task["state"] == "received"
                        and args["digest"] == task["digest"],
                        "unverified evidence",
                    )
                    task.update(
                        state="verified",
                        review=text_field(
                            args["independent_check"], "independent_check"
                        ),
                    )
                else:
                    raise ValueError("unknown task command")
    profile.put("goals", ident, item)
    return item
