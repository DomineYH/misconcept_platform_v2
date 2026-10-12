"""Synthetic, page-sized administrator lists; never imported by runtime."""

from types import SimpleNamespace


def list_fixture(kind, page, total=41, deleted_ids=()):
    ids = [
        row_id for row_id in range(total, 0, -1) if row_id not in deleted_ids
    ]
    total = len(ids)
    visible_ids = ids[(page - 1) * 20 : page * 20]
    group = SimpleNamespace(id=1, name="합성 그룹")
    rows = []
    for row_id in visible_ids:
        if kind == "users":
            rows.append(
                SimpleNamespace(
                    id=row_id,
                    username=f"teacher_{row_id}",
                    nickname=f"교사 {row_id}",
                    role="teacher",
                    group_id=1,
                    group=group,
                    created_at=None,
                )
            )
        else:
            rows.append(
                SimpleNamespace(
                    id=row_id,
                    title=f"시나리오 {row_id}",
                    is_active=1,
                    status="draft",
                    config_version=7,
                    config_json={"problem": {"public_text": "합성 문제"}},
                    review_required=False,
                    subject="수학",
                )
            )
    return dict(
        user=SimpleNamespace(nickname="Admin", role="admin"),
        **{kind: rows},
        groups=[group],
        scenario_group_map={row_id: [1] for row_id in visible_ids},
        session_counts={row_id: 2 for row_id in visible_ids},
        current_page=page,
        total_count=total,
        total_pages=max(1, (total + 19) // 20),
    )
