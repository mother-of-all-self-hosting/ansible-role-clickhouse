<!--
SPDX-FileCopyrightText: 2020 - 2024 MDAD project contributors
SPDX-FileCopyrightText: 2020 - 2024 Slavi Pantaleev
SPDX-FileCopyrightText: 2020 Aaron Raimist
SPDX-FileCopyrightText: 2020 Chris van Dijk
SPDX-FileCopyrightText: 2020 Dominik Zajac
SPDX-FileCopyrightText: 2020 Mickaël Cornière
SPDX-FileCopyrightText: 2022 François Darveau
SPDX-FileCopyrightText: 2022 Julian Foad
SPDX-FileCopyrightText: 2022 Warren Bailey
SPDX-FileCopyrightText: 2023 Antonis Christofides
SPDX-FileCopyrightText: 2023 Felix Stupp
SPDX-FileCopyrightText: 2023 Pierre 'McFly' Marty
SPDX-FileCopyrightText: 2024 - 2025 Suguru Hirahara

SPDX-License-Identifier: AGPL-3.0-or-later
-->

# Setting up ClickHouse

This is an [Ansible](https://www.ansible.com/) role which installs [ClickHouse](https://clickhouse.com) to run as a [Docker](https://www.docker.com/) container wrapped in a systemd service.

ClickHouse is an open-source column-oriented DBMS for online analytical processing (OLAP) that allows users to generate analytical reports using SQL queries in real-time.

See the project's [documentation](https://clickhouse.com/docs) to learn what ClickHouse does and why it might be useful to you.

## Adjusting the playbook configuration

To enable ClickHouse with this role, add the following configuration to your `vars.yml` file.

**Note**: the path should be something like `inventory/host_vars/mash.example.com/vars.yml` if you use the [MASH Ansible playbook](https://github.com/mother-of-all-self-hosting/mash-playbook).

```yaml
########################################################################
#                                                                      #
# clickhouse                                                           #
#                                                                      #
########################################################################

clickhouse_enabled: true

# Put a strong password below, generated with `pwgen -s 64 1` or in another way
clickhouse_root_password: ''

########################################################################
#                                                                      #
# /clickhouse                                                          #
#                                                                      #
########################################################################
```

### Extending the configuration

There are some additional things you may wish to configure about the component.

Take a look at:

- [`defaults/main.yml`](../defaults/main.yml) for some variables that you can customize via your `vars.yml` file. You can override settings (even those that don't have dedicated playbook variables) using the `clickhouse_environment_variables_additional_variables` variable
  - See [this page](https://clickhouse.com/docs/install/docker#configuration) for environment variables
- [`templates/config.yaml.j2`](../templates/config.yaml.j2) for settings that you can customize via your `vars.yml` file with `clickhouse_config_additional_configurations`
  - See [this page](https://clickhouse.com/docs/operations/server-configuration-parameters/settings) for a complete list of ClickHouse's config options

## Installing

After configuring the playbook, run the installation command of your playbook as below:

```sh
ansible-playbook -i inventory/hosts setup.yml --tags=setup-all,start
```

If you use the MASH playbook, the shortcut commands with the [`just` program](https://github.com/mother-of-all-self-hosting/mash-playbook/blob/main/docs/just.md) are also available: `just install-all` or `just setup-all`

## Usage

After running the command for installation, the Clickhouse instance becomes available.

### Getting a database terminal

You can use the CLI installed to the directory specified with `clickhouse_bin_path` (default: `/clickhouse/bin/`) for getting interactive terminal access to the ClickHouse server.

>[!WARNING]
> Modifying the database directly (especially as services are running) is dangerous and may lead to irreversible database corruption. If you are not perfectly sure, create a backup!

### Backing up a database

The directory specified with `clickhouse_backups_path` (default: `/clickhouse/backups/`) is mounted as `/backups` into the container and is an allowed disk for backups called `backups`.

You can export a single database table by using the CLI tool and running a command like this:

```sql
BACKUP TABLE test TO Disk('backups', 'test.zip');
```

Read the [Backup and Restore](https://clickhouse.com/docs/en/operations/backup) article in the official documentation to learn more.

>[!NOTE]
> For better (more en-masse) exporting, it may be beneficial to use the 3rd party [clickhouse-backup](https://github.com/AlexAkulov/clickhouse-backup) tool.

## Upgrading ClickHouse

ClickHouse is supposed to auto-upgrade its data as you upgrade to a newer version. There's nothing special that needs to be done.

## Troubleshooting

### Check the service's logs

You can find the logs in [systemd-journald](https://www.freedesktop.org/software/systemd/man/systemd-journald.service.html) by logging in to the server with SSH and running `journalctl -fu clickhouse` (or how you/your playbook named the service, e.g. `mash-clickhouse`).
