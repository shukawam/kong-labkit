import json

import yaml

from generator.render import render_all
from tests.conftest import ctx_for

USERS = "config/lldap/user-configs/users.json"
GROUPS = "config/lldap/group-configs/groups.json"

DEV_AND_RESEARCHER = {
    "type": "ldap",
    "users": [
        {"name": "developer", "email": "developer@example.com", "groups": ["developer-dep"]},
        {
            "name": "researcher",
            "email": "researcher@example.com",
            "groups": ["developer-dep", "researcher-dep"],
        },
    ],
}


def json_stream(text: str) -> list[dict]:
    """bootstrap.sh は jq に食わせるだけなので、設定ファイルは配列ではなく値の並び。"""
    decoder = json.JSONDecoder()
    values, index = [], 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        value, index = decoder.raw_decode(text, index)
        values.append(value)
    return values


def test_bootstrap_configs_are_emitted_only_for_ldap():
    files = render_all(ctx_for(idp={"type": "ldap"}))
    assert USERS in files
    assert GROUPS in files
    assert USERS not in render_all(ctx_for())
    assert USERS not in render_all(ctx_for(idp={"type": "keycloak", "realm": "acme"}))


def test_default_configs_hold_the_single_test_user():
    ctx = ctx_for(idp={"type": "ldap"})
    files = render_all(ctx)
    assert [g["name"] for g in json_stream(files[GROUPS])] == ["acme-ai-users"]
    user = json_stream(files[USERS])[0]
    assert user["id"] == "tester"
    assert user["password"] == "tester-password"
    assert user["groups"] == ["acme-ai-users"]
    # bootstrap.sh は省略された任意フィールドを lldap 側でも消すため、明示する
    assert user["email"] == "tester@example.com"


def test_every_user_and_group_is_emitted_as_its_own_value():
    files = render_all(ctx_for(idp=DEV_AND_RESEARCHER))
    assert [g["name"] for g in json_stream(files[GROUPS])] == ["developer-dep", "researcher-dep"]
    users = json_stream(files[USERS])
    assert [u["id"] for u in users] == ["developer", "researcher"]
    assert users[1]["groups"] == ["developer-dep", "researcher-dep"]
    assert users[1]["email"] == "researcher@example.com"


def test_a_json_array_would_not_be_accepted():
    # jq に配列を渡すと 1 件の値として読まれ、ユーザーが 1 人も作られない
    body = render_all(ctx_for(idp=DEV_AND_RESEARCHER))[USERS]
    assert not body.lstrip().startswith("[")


def test_bootstrap_users_match_the_kong_consumers():
    files = render_all(ctx_for(idp=DEV_AND_RESEARCHER))
    users = [u["id"] for u in json_stream(files[USERS])]
    consumers = yaml.safe_load(files["config/kong/kong.yaml"])["consumers"]
    assert [c["username"] for c in consumers] == users
