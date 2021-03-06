# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from __future__ import print_function
import json
import os

from knack.config import get_config_parser
from knack.log import get_logger
from knack.prompting import prompt, prompt_y_n, prompt_choice_list, prompt_pass, NoTTYException
from knack.util import CLIError

from azure.cli.core.util import ConfiguredDefaultSetter

from azure.cli.command_modules.configure._consts import (OUTPUT_LIST, LOGIN_METHOD_LIST,
                                                         MSG_INTRO,
                                                         MSG_CLOSING,
                                                         MSG_GLOBAL_SETTINGS_LOCATION,
                                                         MSG_HEADING_CURRENT_CONFIG_INFO,
                                                         MSG_HEADING_ENV_VARS,
                                                         MSG_PROMPT_MANAGE_GLOBAL,
                                                         MSG_PROMPT_GLOBAL_OUTPUT,
                                                         MSG_PROMPT_LOGIN,
                                                         MSG_PROMPT_TELEMETRY,
                                                         MSG_PROMPT_FILE_LOGGING,
                                                         MSG_PROMPT_CACHE_TTL,
                                                         DEFAULT_CACHE_TTL)
from azure.cli.command_modules.configure._utils import get_default_from_config

answers = {}

logger = get_logger(__name__)


def _print_cur_configuration(file_config):
    from azure.cli.core._config import ENV_VAR_PREFIX
    print(MSG_HEADING_CURRENT_CONFIG_INFO)
    for section in file_config.sections():
        print()
        print('[{}]'.format(section))
        for option in file_config.options(section):
            print('{} = {}'.format(option, file_config.get(section, option)))
    env_vars = [ev for ev in os.environ if ev.startswith(ENV_VAR_PREFIX)]
    if env_vars:
        print(MSG_HEADING_ENV_VARS)
        print('\n'.join(['{} = {}'.format(ev, os.environ[ev]) for ev in env_vars]))


def _config_env_public_azure(cli_ctx, _):
    from adal.adal_error import AdalError
    from azure.cli.core.commands.client_factory import get_mgmt_service_client
    from azure.mgmt.resource import ResourceManagementClient
    from azure.cli.core._profile import Profile
    # Determine if user logged in
    try:
        list(get_mgmt_service_client(cli_ctx, ResourceManagementClient).resources.list())
    except CLIError:
        # Not logged in
        login_successful = False
        while not login_successful:
            method_index = prompt_choice_list(MSG_PROMPT_LOGIN, LOGIN_METHOD_LIST)
            answers['login_index'] = method_index
            answers['login_options'] = str(LOGIN_METHOD_LIST)
            profile = Profile(cli_ctx=cli_ctx)
            interactive = False
            username = None
            password = None
            service_principal = None
            tenant = None
            if method_index == 0:  # device auth
                interactive = True
            elif method_index == 1:  # username and password
                username = prompt('Username: ')
                password = prompt_pass(msg='Password: ')
            elif method_index == 2:  # service principal with secret
                service_principal = True
                username = prompt('Service principal: ')
                tenant = prompt('Tenant: ')
                password = prompt_pass(msg='Client secret: ')
            elif method_index == 3:  # skip
                return
            try:
                profile.find_subscriptions_on_login(
                    interactive,
                    username,
                    password,
                    service_principal,
                    tenant)
                login_successful = True
                logger.warning('Login successful!')
            except AdalError as err:
                logger.error('Login error!')
                logger.error(err)


def _handle_global_configuration(config):
    # print location of global configuration
    print(MSG_GLOBAL_SETTINGS_LOCATION.format(config.config_path))
    # set up the config parsers
    file_config = get_config_parser()
    config_exists = file_config.read([config.config_path])
    should_modify_global_config = False
    if config_exists:
        # print current config and prompt to allow global config modification
        _print_cur_configuration(file_config)
        should_modify_global_config = prompt_y_n(MSG_PROMPT_MANAGE_GLOBAL, default='n')
        answers['modify_global_prompt'] = should_modify_global_config
    if not config_exists or should_modify_global_config:
        # no config exists yet so configure global config or user wants to modify global config
        with ConfiguredDefaultSetter(config, False):
            output_index = prompt_choice_list(MSG_PROMPT_GLOBAL_OUTPUT, OUTPUT_LIST,
                                              default=get_default_from_config(config,
                                                                              'core', 'output',
                                                                              OUTPUT_LIST))
            answers['output_type_prompt'] = output_index
            answers['output_type_options'] = str(OUTPUT_LIST)
            enable_file_logging = prompt_y_n(MSG_PROMPT_FILE_LOGGING, default='n')
            allow_telemetry = prompt_y_n(MSG_PROMPT_TELEMETRY, default='y')
            answers['telemetry_prompt'] = allow_telemetry
            cache_ttl = None
            while not cache_ttl:
                try:
                    cache_ttl = prompt(MSG_PROMPT_CACHE_TTL) or DEFAULT_CACHE_TTL
                    # ensure valid int by casting
                    cache_value = int(cache_ttl)
                    if cache_value < 1:
                        raise ValueError
                except ValueError:
                    logger.error('TTL must be a positive integer')
                    cache_ttl = None
            # save the global config
            config.set_value('core', 'output', OUTPUT_LIST[output_index]['name'])
            config.set_value('core', 'collect_telemetry', 'yes' if allow_telemetry else 'no')
            config.set_value('core', 'cache_ttl', cache_ttl)
            config.set_value('logging', 'enable_log_file', 'yes' if enable_file_logging else 'no')


# pylint: disable=inconsistent-return-statements
def handle_configure(cmd, defaults=None, list_defaults=None, scope=None):
    if defaults:
        defaults_section = cmd.cli_ctx.config.defaults_section_name
        with ConfiguredDefaultSetter(cmd.cli_ctx.config, scope.lower() == 'local'):
            for default in defaults:
                parts = default.split('=', 1)
                if len(parts) == 1:
                    raise CLIError('usage error: --defaults STRING=STRING STRING=STRING ...')
                cmd.cli_ctx.config.set_value(defaults_section, parts[0], _normalize_config_value(parts[1]))
        return
    if list_defaults:
        with ConfiguredDefaultSetter(cmd.cli_ctx.config, scope.lower() == 'local'):
            defaults_result = cmd.cli_ctx.config.items(cmd.cli_ctx.config.defaults_section_name)
        return [x for x in defaults_result if x.get('value')]

    # if nothing supplied, we go interactively
    try:
        print(MSG_INTRO)
        _handle_global_configuration(cmd.cli_ctx.config)
        print(MSG_CLOSING)
        # TODO: log_telemetry('configure', **answers)
    except NoTTYException:
        raise CLIError('This command is interactive and no tty available.')
    except (EOFError, KeyboardInterrupt):
        print()


def _normalize_config_value(value):
    if value:
        value = '' if value in ["''", '""'] else value
    return value


def _get_cache_directory(cli_ctx):
    from azure.cli.core.commands.client_factory import get_subscription_id
    from azure.cli.core._environment import get_config_dir
    return os.path.join(
        get_config_dir(),
        'object_cache',
        cli_ctx.cloud.name,
        get_subscription_id(cli_ctx))


def list_cache_contents(cmd):
    from glob import glob
    directory = _get_cache_directory(cmd.cli_ctx)
    contents = []
    rg_paths = glob(os.path.join(directory, '*'))
    for rg_path in rg_paths:
        rg_name = os.path.split(rg_path)[1]
        for dir_name, _, file_list in os.walk(rg_path):
            if not file_list:
                continue
            resource_type = os.path.split(dir_name)[1]
            for f in file_list:
                file_path = os.path.join(dir_name, f)
                try:
                    with open(file_path, 'r') as cache_file:
                        cache_obj = json.loads(cache_file.read())
                        contents.append({
                            'resourceGroup': rg_name,
                            'resourceType': resource_type,
                            'name': f.split('.', 1)[0],
                            'lastSaved': cache_obj['last_saved']
                        })
                except KeyError:
                    # invalid cache entry
                    logger.debug('Removing corrupt cache file: %s', file_path)
                    os.remove(file_path)
    return contents


def show_cache_contents(cmd, resource_group_name, item_name, resource_type):
    directory = _get_cache_directory(cmd.cli_ctx)
    item_path = os.path.join(directory, resource_group_name, resource_type, '{}.json'.format(item_name))
    try:
        with open(item_path, 'r') as cache_file:
            cache_obj = json.loads(cache_file.read())
    except (OSError, IOError):
        raise CLIError('Not found in cache: {}'.format(item_path))
    return cache_obj['_payload']


def delete_cache_contents(cmd, resource_group_name, item_name, resource_type):
    directory = _get_cache_directory(cmd.cli_ctx)
    item_path = os.path.join(directory, resource_group_name, resource_type, '{}.json'.format(item_name))
    try:
        os.remove(item_path)
    except (OSError, IOError):
        logger.info('%s not found in object cache.', item_path)


def purge_cache_contents():
    import shutil
    from azure.cli.core._environment import get_config_dir
    directory = os.path.join(get_config_dir(), 'object_cache')
    try:
        shutil.rmtree(directory)
    except (OSError, IOError) as ex:
        logger.debug(ex)
