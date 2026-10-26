#!/usr/bin/python
# SPDX-FileCopyrightText: 2026 Slavi Pantaleev
#
# SPDX-License-Identifier: AGPL-3.0-or-later

DOCUMENTATION = r"""
---
module: clickhouse_managed_databases
short_description: Prepares managed ClickHouse databases (users, passwords, databases, privileges)
description:
  - Inspects the server first and only runs the statements needed to bring each managed database
    (and its user) to the desired state, so that it can report whether anything changed.
  - Talks to ClickHouse by running C(clickhouse-client) in its container (via C(docker exec)) and feeding it SQL on stdin,
    so that passwords never end up in files or on command lines.
    The client authenticates as the superuser via the container's own C(CLICKHOUSE_USER) and C(CLICKHOUSE_PASSWORD) environment variables.
  - ClickHouse does not expose password hashes, so whether a user's password is the desired one is checked by logging in as that user.
    A failed attempt shows up as an error in the server log, but that only happens when the password is about to be changed anyway.
  - Users authenticate via C(sha256_password). New passwords are sent as salted hashes (C(IDENTIFIED WITH sha256_hash)),
    so that they do not show up in server logs. A user authenticated in any other way (e.g. C(plaintext_password),
    which this module's predecessor used) gets its password set again.
  - The additional SQL of each database is run every time (unless in check mode), as there is no telling whether it would change anything.
    It does not count as a change.
options:
  docker_command:
    description: The command (as a list of arguments) which invokes the Docker CLI.
    type: list
    elements: str
    required: true
  container:
    description: The name of the (running) ClickHouse container.
    type: str
    required: true
  databases:
    description: The managed databases.
    type: list
    elements: dict
    required: true
    options:
      name:
        type: str
        required: true
      username:
        type: str
        required: true
      password:
        type: str
        required: true
        no_log: true
      additional_sql:
        description: SQL statements to run after preparing the database.
        type: str
        default: ""
  connection_retries:
    description: How many times to retry when the client cannot connect to ClickHouse.
    type: int
    default: 0
  connection_retry_delay_seconds:
    description: How long to wait between connection retries.
    type: int
    default: 3
"""

RETURN = r"""
changes:
  description: Human-readable descriptions of the changes made (or which would be made, in check mode).
  type: list
  elements: str
  returned: always
"""

import binascii
import hashlib
import json
import os
import re
import time

from ansible.module_utils.basic import AnsibleModule

CLIENT = "clickhouse-client"

# Errors which mean that the server could not be reached (yet), as opposed to it rejecting something:
# SOCKET_TIMEOUT (209), NETWORK_ERROR (210, e.g. "Connection refused" while starting),
# or the container not running yet.
CONNECTION_ERROR_RE = re.compile(r"Code: (209|210)\.|is not running")

AUTHENTICATION_FAILED_RE = re.compile(r"Code: 516\.")

PASSWORD_AUTH_TYPE = "sha256_password"

# Privileges grantable on a database (`GRANT ALL ON db.*` grants all of these, and only these).
DATABASE_LEVELS = ("DATABASE", "TABLE", "VIEW", "DICTIONARY", "COLUMN")


def quote_string(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def quote_ident(value):
    return "`" + value.replace("\\", "\\\\").replace("`", "\\`") + "`"


def sha256_hash(password, salt):
    return hashlib.sha256(password.encode("utf-8") + salt.encode("ascii")).hexdigest().upper()


class Client(object):
    def __init__(self, module):
        self.module = module
        self.docker = module.params["docker_command"]
        self.container = module.params["container"]

    def _run(self, sql, docker_args=None, environ=None):
        """Runs the client and returns its result, retrying while ClickHouse cannot be connected to."""
        args = self.docker + ["exec", "-i"] + (docker_args or []) + [self.container, CLIENT]
        attempts = 1 + max(self.module.params["connection_retries"], 0)
        for attempt in range(1, attempts + 1):
            rc, stdout, stderr = self.module.run_command(args, data=sql, binary_data=True, environ_update=environ)
            if rc == 0 or not CONNECTION_ERROR_RE.search(stderr) or attempt == attempts:
                break
            time.sleep(self.module.params["connection_retry_delay_seconds"])
        return rc, stdout, stderr

    def fail(self, rc, stderr):
        self.module.fail_json(
            msg="Preparing the managed databases failed: clickhouse-client exited with code %d.\nOutput:\n%s" % (rc, stderr),
            rc=rc,
            stderr=stderr,
        )

    def run(self, sql):
        rc, stdout, stderr = self._run(sql)
        if rc != 0:
            self.fail(rc, stderr)
        return stdout

    def query(self, sql):
        """Runs SELECT statements (ending in `FORMAT JSONEachRow`) and returns all of their rows."""
        return [json.loads(line) for line in self.run(sql).splitlines() if line.strip()]

    def can_log_in(self, username, password):
        """Tells whether `username` can log in with `password`."""
        rc, stdout, stderr = self._run(
            "SELECT currentUser()",
            # Without a value, `docker exec` takes these from its own environment, overriding the container's.
            ["-e", "CLICKHOUSE_USER", "-e", "CLICKHOUSE_PASSWORD"],
            {"CLICKHOUSE_USER": username, "CLICKHOUSE_PASSWORD": password},
        )
        if rc == 0 and stdout.strip() == username:
            return True
        if rc != 0 and AUTHENTICATION_FAILED_RE.search(stderr):
            return False
        self.fail(rc, stderr)


def inspect_server(client, databases):
    usernames = ", ".join(quote_string(u) for u in sorted({db["username"] for db in databases}))
    names = ", ".join(quote_string(n) for n in sorted({db["name"] for db in databases}))
    # Each row is tagged with what it is about, as all of them come back as a single stream.
    rows = client.query("""
SELECT 'database' AS kind, name FROM system.databases WHERE name IN (%(names)s) FORMAT JSONEachRow;
SELECT 'user' AS kind, name, storage, auth_type FROM system.users WHERE name IN (%(users)s) FORMAT JSONEachRow;
SELECT 'grant' AS kind, user_name, access_type, database, table, column, is_partial_revoke
    FROM system.grants WHERE user_name IN (%(users)s) FORMAT JSONEachRow;
SELECT 'privilege' AS kind, privilege, level, parent_group FROM system.privileges FORMAT JSONEachRow;
""" % dict(names=names, users=usernames))

    def of_kind(kind):
        return [row for row in rows if row["kind"] == kind]

    users = {}
    for row in of_kind("user"):
        auth_type = row["auth_type"]
        # A single string until ClickHouse 24.9 (which added support for several authentication methods), an array since.
        users[row["name"]] = dict(storage=row["storage"], auth_types=[auth_type] if isinstance(auth_type, str) else auth_type)

    return dict(
        databases=set(row["name"] for row in of_kind("database")),
        users=users,
        grants=of_kind("grant"),
        privileges=of_kind("privilege"),
    )


def has_all_privileges(server, username, name):
    """Tells whether the user has all privileges on the database (what `GRANT ALL ON db.*` grants)."""
    privileges = server["privileges"]
    parents = dict((p["privilege"], p["parent_group"]) for p in privileges)
    groups = set(p["parent_group"] for p in privileges if p["parent_group"])
    # Which privileges exist (and how they are grouped) depends on the version, so this goes by the server's own list.
    required = [p["privilege"] for p in privileges if p["privilege"] not in groups and p["level"] in DATABASE_LEVELS]

    granted = set()
    for grant in server["grants"]:
        if grant["user_name"] != username or grant["database"] not in (None, name):
            continue
        if grant["is_partial_revoke"]:
            # Something in (or below) this database is revoked. Granting all privileges again takes care of it.
            return False
        if grant["table"] is None and grant["column"] is None:
            granted.add(grant["access_type"])

    def is_granted(privilege):
        while privilege is not None:
            if privilege in granted:
                return True
            privilege = parents.get(privilege)
        return False

    return all(is_granted(privilege) for privilege in required)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            docker_command=dict(type="list", elements="str", required=True),
            container=dict(type="str", required=True),
            databases=dict(
                type="list",
                elements="dict",
                required=True,
                options=dict(
                    name=dict(type="str", required=True),
                    username=dict(type="str", required=True),
                    password=dict(type="str", required=True, no_log=True),
                    additional_sql=dict(type="str", default=""),
                ),
            ),
            connection_retries=dict(type="int", default=0),
            connection_retry_delay_seconds=dict(type="int", default=3),
        ),
        supports_check_mode=True,
    )

    databases = module.params["databases"]
    if not databases:
        module.exit_json(changed=False, changes=[])

    client = Client(module)
    server = inspect_server(client, databases)
    users = server["users"]

    changes = []
    statements = []
    # Several managed databases may share a user. Its password is the last one given for it.
    passwords_set = {}
    for db in databases:
        name, username, password = db["name"], db["username"], db["password"]
        user = users.get(username)

        if user is not None and user["storage"] != "local_directory":
            module.fail_json(
                msg="The user `%s` of the managed database `%s` is defined in `%s` storage, so it cannot be managed via SQL."
                % (username, name, user["storage"])
            )

        if username in passwords_set:
            password_ok = passwords_set[username] == password
        else:
            password_ok = (
                user is not None
                and user["auth_types"] == [PASSWORD_AUTH_TYPE]
                and client.can_log_in(username, password)
            )

        if not password_ok:
            salt = binascii.hexlify(os.urandom(16)).decode("ascii")
            password_hash = sha256_hash(password, salt)
            module.no_log_values.add(password_hash)
            identified = "IDENTIFIED WITH sha256_hash BY '%s' SALT '%s'" % (password_hash, salt)
            if user is None:
                changes.append("create user %s" % quote_ident(username))
                statements.append("CREATE USER %s %s;" % (quote_ident(username), identified))
                users[username] = dict(storage="local_directory", auth_types=[PASSWORD_AUTH_TYPE])
            elif user["auth_types"] != [PASSWORD_AUTH_TYPE]:
                changes.append(
                    "set the password of user %s (authenticated via %s, from now on via %s)"
                    % (quote_ident(username), ", ".join(user["auth_types"]), PASSWORD_AUTH_TYPE)
                )
                statements.append("ALTER USER %s %s;" % (quote_ident(username), identified))
            else:
                changes.append("set the password of user %s" % quote_ident(username))
                statements.append("ALTER USER %s %s;" % (quote_ident(username), identified))
        passwords_set[username] = password

        if name not in server["databases"]:
            changes.append("create database %s" % quote_ident(name))
            statements.append("CREATE DATABASE %s;" % quote_ident(name))
            server["databases"].add(name)

        if not has_all_privileges(server, username, name):
            changes.append("grant all privileges on database %s to %s" % (quote_ident(name), quote_ident(username)))
            statements.append("GRANT ALL ON %s.* TO %s;" % (quote_ident(name), quote_ident(username)))
            server["grants"].append(dict(
                user_name=username, access_type="ALL", database=name, table=None, column=None, is_partial_revoke=0,
            ))

    for db in databases:
        additional_sql = db["additional_sql"].strip()
        if additional_sql:
            statements.append(additional_sql if additional_sql.endswith(";") else additional_sql + ";")

    changed = len(changes) > 0
    result = dict(changed=changed, changes=changes)
    if module._diff:
        result["diff"] = dict(before="", after="".join("%s\n" % c for c in changes))

    if module.check_mode or not statements:
        module.exit_json(**result)

    client.run("\n".join(statements) + "\n")
    module.exit_json(**result)


if __name__ == "__main__":
    main()
