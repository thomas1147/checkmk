#!/usr/bin/env python
# -*- encoding: utf-8; py-indent-offset: 4 -*-
# +------------------------------------------------------------------+
# |             ____ _               _        __  __ _  __           |
# |            / ___| |__   ___  ___| | __   |  \/  | |/ /           |
# |           | |   | '_ \ / _ \/ __| |/ /   | |\/| | ' /            |
# |           | |___| | | |  __/ (__|   <    | |  | | . \            |
# |            \____|_| |_|\___|\___|_|\_\___|_|  |_|_|\_\           |
# |                                                                  |
# | Copyright Mathias Kettner 2014             mk@mathias-kettner.de |
# +------------------------------------------------------------------+
#
# This file is part of Check_MK.
# The official homepage is at http://mathias-kettner.de/check_mk.
#
# check_mk is free software;  you can redistribute it and/or modify it
# under the  terms of the  GNU General Public License  as published by
# the Free Software Foundation in version 2.  check_mk is  distributed
# in the hope that it will be useful, but WITHOUT ANY WARRANTY;  with-
# out even the implied warranty of  MERCHANTABILITY  or  FITNESS FOR A
# PARTICULAR PURPOSE. See the  GNU General Public License for more de-
# tails. You should have  received  a copy of the  GNU  General Public
# License along with GNU Make; see the file  COPYING.  If  not,  write
# to the Free Software Foundation, Inc., 51 Franklin St,  Fifth Floor,
# Boston, MA 02110-1301 USA.
"""Module to hold shared code for internals and the plugins"""

# TODO: More feature related splitting up would be better

import abc
import os
import time
import re
import inspect
import hashlib
import traceback
import types

import livestatus

import cmk.utils.plugin_registry
import cmk.utils.render
import cmk.utils.regex

import cmk.gui.config as config
import cmk.gui.sites as sites
import cmk.gui.visuals as visuals
import cmk.gui.forms as forms
import cmk.gui.utils
import cmk.gui.view_utils
from cmk.gui.valuespec import ValueSpec  # pylint: disable=unused-import
from cmk.gui.log import logger
from cmk.gui.htmllib import HTML
from cmk.gui.i18n import _
from cmk.gui.globals import html
from cmk.gui.exceptions import MKGeneralException
from cmk.gui.display_options import display_options
from cmk.gui.permissions import permission_registry


# TODO: Better name it PainterOptions or DisplayOptions? There are options which only affect
# painters, but some which affect generic behaviour of the views, so DisplayOptions might
# be better.
class PainterOptions(object):
    """Painter options are settings that can be changed per user per view.
    These options are controlled throught the painter options form which
    is accessible through the small monitor icon on the top left of the
    views."""

    def __init__(self):
        super(PainterOptions, self).__init__()
        # The names of the painter options used by the current view
        self._used_option_names = None
        # The effective options for this view
        self._options = {}

    def load(self, view_name=None):
        self._load_from_config(view_name)

    # Load the options to be used for this view
    def _load_used_options(self, view):
        if self._used_option_names is not None:
            return  # only load once per request

        options = set([])

        for cell in get_group_cells(view) + get_cells(view):
            options.update(cell.painter_options())

        # Also layouts can register painter options
        layout_class = layout_registry.get(view.get("layout"))
        if layout_class:
            options.update(layout_class().painter_options)

        # TODO: Improve sorting. Add a sort index?
        self._used_option_names = sorted(options)

    def _load_from_config(self, view_name):
        if self._is_anonymous_view(view_name):
            return  # never has options

        if not self.painter_options_permitted():
            return

        # Options are stored per view. Get all options for all views
        vo = config.user.load_file("viewoptions", {})
        self._options = vo.get(view_name, {})

    def _is_anonymous_view(self, view_name):
        return view_name is None

    def save_to_config(self, view_name):
        vo = config.user.load_file("viewoptions", {}, lock=True)
        vo[view_name] = self._options
        config.user.save_file("viewoptions", vo)

    def update_from_url(self, view_name, view):
        self._load_used_options(view)

        if not self.painter_option_form_enabled():
            return

        if html.request.has_var("_reset_painter_options"):
            self._clear_painter_options(view_name)
            return

        elif html.request.has_var("_update_painter_options"):
            self._set_from_submitted_form(view_name)

    def _set_from_submitted_form(self, view_name):
        # TODO: Remove all keys that are in painter_option_registry
        # but not in self._used_option_names

        modified = False
        for option_name in self._used_option_names:
            # Get new value for the option from the value spec
            vs = self.get_valuespec_of(option_name)
            value = vs.from_html_vars("po_%s" % option_name)

            if not self._is_set(option_name) or self.get(option_name) != value:
                modified = True

            self.set(option_name, value)

        if modified:
            self.save_to_config(view_name)

    def _clear_painter_options(self, view_name):
        # TODO: This never removes options that are not existant anymore
        modified = False
        for name in painter_option_registry.keys():
            try:
                del self._options[name]
                modified = True
            except KeyError:
                pass

        if modified:
            self.save_to_config(view_name)

        # Also remove the options from current html vars. Otherwise the
        # painter option form will display the just removed options as
        # defaults of the painter option form.
        for varname, _value in list(html.request.itervars(prefix="po_")):
            html.request.del_var(varname)

    def get_valuespec_of(self, name):
        return painter_option_registry[name]().valuespec

    def _is_set(self, name):
        return name in self._options

    # Sets a painter option value (only for this request). Is not persisted!
    def set(self, name, value):
        self._options[name] = value

    # Returns either the set value, the provided default value or if none
    # provided, it returns the default value of the valuespec.
    def get(self, name, dflt=None):
        if dflt is None:
            try:
                dflt = self.get_valuespec_of(name).default_value()
            except KeyError:
                # Some view options (that are not declared as display options)
                # like "refresh" don't have a valuespec. So they need to default
                # to None.
                # TODO: Find all occurences and simply declare them as "invisible"
                # painter options.
                pass
        return self._options.get(name, dflt)

    # Not falling back to a default value, simply returning None in case
    # the option is not set.
    def get_without_default(self, name):
        return self._options.get(name)

    def get_all(self):
        return self._options

    def painter_options_permitted(self):
        return config.user.may("general.painter_options")

    def painter_option_form_enabled(self):
        return self._used_option_names and self.painter_options_permitted()

    def show_form(self, view):
        self._load_used_options(view)

        if not display_options.enabled(display_options.D) or not self.painter_option_form_enabled():
            return

        html.open_div(id_="painteroptions", class_=["view_form"], style="display: none;")
        html.begin_form("painteroptions")
        forms.header(_("Display Options"))
        for name in self._used_option_names:
            vs = self.get_valuespec_of(name)
            forms.section(vs.title())
            # TODO: Possible improvement for vars which default is specified
            # by the view: Don't just default to the valuespecs default. Better
            # use the view default value here to get the user the current view
            # settings reflected.
            vs.render_input("po_%s" % name, self.get(name))
        forms.end()

        html.button("_update_painter_options", _("Submit"), "submit")
        html.button("_reset_painter_options", _("Reset"), "submit")

        html.hidden_fields()
        html.end_form()
        html.close_div()


# Calculates a uniq id for each data row which identifies the current
# row accross different page loadings.
def row_id(view, row):
    key = u''
    for col in multisite_datasources[view['datasource']]['idkeys']:
        key += u'~%s' % row[col]
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def get_painter_columns(painter):
    if callable(painter["columns"]):
        return painter["columns"]()
    return painter["columns"]


# The Group-value of a row is used for deciding whether
# two rows are in the same group or not
def group_value(row, group_cells):
    group = []
    for cell in group_cells:
        painter = cell.painter()

        groupvalfunc = painter.get("groupby")
        if groupvalfunc:
            if "args" in painter:
                group.append(groupvalfunc(row, *painter["args"]))
            else:
                group.append(groupvalfunc(row))

        else:
            for c in get_painter_columns(painter):
                if c in row:
                    group.append(row[c])

    return _create_dict_key(group)


def _create_dict_key(value):
    if isinstance(value, (list, tuple)):
        return tuple(map(_create_dict_key, value))
    elif isinstance(value, dict):
        return tuple([(k, _create_dict_key(v)) for (k, v) in sorted(value.items())])
    return value


class PainterOption(object):
    __metaclass__ = abc.ABCMeta

    @abc.abstractproperty
    def ident(self):
        # type: () -> str
        """The identity of a painter option. One word, may contain alpha numeric characters"""
        raise NotImplementedError()

    @abc.abstractproperty
    def valuespec(self):
        # type: () -> ValueSpec
        raise NotImplementedError()


class ViewPainterOptionRegistry(cmk.utils.plugin_registry.ClassRegistry):
    def plugin_base_class(self):
        return PainterOption

    def plugin_name(self, plugin_class):
        return plugin_class().ident


painter_option_registry = ViewPainterOptionRegistry()


class Layout(object):
    __metaclass__ = abc.ABCMeta

    @abc.abstractproperty
    def ident(self):
        # type: () -> str
        """The identity of a layout. One word, may contain alpha numeric characters"""
        raise NotImplementedError()

    @abc.abstractproperty
    def title(self):
        # type: () -> Text
        """Short human readable title of the layout"""
        raise NotImplementedError()

    @abc.abstractmethod
    def render(self, rows, view, group_cells, cells, num_columns, show_checkboxes):
        # type: (List, Dict, List[Cell], List[Cell], int, bool) -> None
        """Render the given data in this layout"""
        raise NotImplementedError()

    @abc.abstractproperty
    def can_display_checkboxes(self):
        # type: () -> bool
        """Whether this layout can display checkboxes for selecting rows"""
        raise NotImplementedError()

    @abc.abstractproperty
    def is_hidden(self):
        # type: () -> bool
        """Whether this should be hidden from the user (e.g. in the view editor layout choice)"""
        raise NotImplementedError()

    @property
    def painter_options(self):
        # type: () -> List[str]
        """Returns the painter option identities used by this layout"""
        return []

    @property
    def has_individual_csv_export(self):
        # type: () -> bool
        """Whether this layout has an individual CSV export implementation"""
        return False

    def csv_export(self, rows, view, group_cells, cells):
        # type: (List, Dict, List[Cell], List[Cell]) -> None
        """Render the given data using this layout for CSV"""
        pass


class ViewLayoutRegistry(cmk.utils.plugin_registry.ClassRegistry):
    def plugin_base_class(self):
        return Layout

    def plugin_name(self, plugin_class):
        return plugin_class().ident

    def get_choices(self):
        choices = []
        for plugin_class in self.values():
            layout = plugin_class()
            if layout.is_hidden:
                continue

            choices.append((layout.ident, layout.title))

        return choices


layout_registry = ViewLayoutRegistry()


class CommandGroup(object):
    __metaclass__ = abc.ABCMeta

    @abc.abstractproperty
    def ident(self):
        # type: () -> str
        """The identity of a command group. One word, may contain alpha numeric characters"""
        raise NotImplementedError()

    @abc.abstractproperty
    def title(self):
        # type: () -> Text
        raise NotImplementedError()

    @abc.abstractproperty
    def sort_index(self):
        # type: () -> int
        raise NotImplementedError()


class CommandGroupRegistry(cmk.utils.plugin_registry.ClassRegistry):
    def plugin_base_class(self):
        return CommandGroup

    def plugin_name(self, plugin_class):
        return plugin_class().ident


command_group_registry = CommandGroupRegistry()


# TODO: Kept for pre 1.6 compatibility
def register_command_group(ident, title, sort_index):
    cls = type(
        "LegacyCommandGroup%s" % ident.title(), (CommandGroup,), {
            "_ident": ident,
            "_title": title,
            "_sort_index": sort_index,
            "ident": property(lambda s: s._ident),
            "title": property(lambda s: s._title),
            "sort_index": property(lambda s: s._sort_index),
        })
    command_group_registry.register(cls)


class Command(object):
    __metaclass__ = abc.ABCMeta

    @abc.abstractproperty
    def ident(self):
        # type: () -> str
        """The identity of a command. One word, may contain alpha numeric characters"""
        raise NotImplementedError()

    @abc.abstractproperty
    def title(self):
        # type: () -> Text
        raise NotImplementedError()

    @abc.abstractproperty
    def permission(self):
        # type: () -> Type[Permission]
        raise NotImplementedError()

    @abc.abstractproperty
    def tables(self):
        # type: () -> List[str]
        """List of livestatus table identities the action may be used with"""
        raise NotImplementedError()

    def render(self, what):
        # type: (str) -> None
        raise NotImplementedError()

    @abc.abstractmethod
    def action(self, cmdtag, spec, row, row_index, num_rows):
        # type: (str, str, dict, int, int) -> Optional[Tuple[List[str], Text]]
        raise NotImplementedError()

    @property
    def group(self):
        # type: () -> Type[CommandGroup]
        """The command group the commmand belongs to"""
        return command_group_registry["various"]

    @property
    def only_view(self):
        # type: () -> Optional[str]
        """View name to show a view exclusive command for"""
        return None

    def executor(self, command, site):
        # type: (str, str) -> Callable
        """Function that is called to execute this action"""
        sites.live().command("[%d] %s" % (int(time.time()), command), site)


class CommandRegistry(cmk.utils.plugin_registry.ClassRegistry):
    def plugin_base_class(self):
        return Command

    def plugin_name(self, plugin_class):
        return plugin_class().ident


command_registry = CommandRegistry()


# TODO: Kept for pre 1.6 compatibility
def register_legacy_command(spec):
    ident = re.sub("[^a-zA-Z]", "", spec["title"]).lower()
    cls = type(
        "LegacyCommand%s" % ident.title(), (Command,), {
            "_ident": ident,
            "_spec": spec,
            "ident": property(lambda s: s._ident),
            "title": property(lambda s: s._spec["title"]),
            "permission": property(lambda s: permission_registry[s._spec["permission"]]),
            "tables": property(lambda s: s._spec["tables"]),
            "render": lambda s: s._spec["render"](),
            "action":
                lambda s, cmdtag, spec, row, row_index, num_rows: s._spec["action"](cmdtag, spec, row),
            "group": lambda s: command_group_registry[s._spec.get("group", "various")],
            "only_view": lambda s: s._spec.get("only_view"),
        })
    command_registry.register(cls)


# TODO: Refactor to plugin_registries
multisite_datasources = {}
multisite_painters = {}
multisite_sorters = {}
multisite_builtin_views = {}
view_hooks = {}
inventory_displayhints = {}
# For each view a function can be registered that has to return either True
# or False to show a view as context link
view_is_enabled = {}


def view_title(view):
    return visuals.visual_title('view', view)


# TODO: Move this to view processing code. This must not be module global as
# it contains request specific information
painter_options = PainterOptions()


def transform_action_url(url_spec):
    if isinstance(url_spec, tuple):
        return url_spec
    return (url_spec, None)


def is_stale(row):
    return row.get('service_staleness', row.get('host_staleness', 0)) >= config.staleness_threshold


def paint_stalified(row, text):
    if is_stale(row):
        return "stale", text
    return "", text


def paint_host_list(site, hosts):
    return "", ", ".join(cmk.gui.view_utils.get_host_list_links(site, hosts))


def format_plugin_output(output, row):
    return cmk.gui.view_utils.format_plugin_output(
        output, row, shall_escape=config.escape_plugin_output)


def link_to_view(content, row, view_name):
    if display_options.disabled(display_options.I):
        return content

    url = url_to_view(row, view_name)
    if url:
        return html.render_a(content, href=url)
    return content


# TODO: There is duplicated logic with visuals.collect_context_links_of()
def url_to_view(row, view_name):
    if display_options.disabled(display_options.I):
        return None

    view = get_permitted_views(load_all_views()).get(view_name)
    if view:
        # Get the context type of the view to link to, then get the parameters of this
        # context type and try to construct the context from the data of the row
        url_vars = []
        datasource = multisite_datasources[view['datasource']]
        for info_key in datasource['infos']:
            if info_key in view['single_infos']:
                # Determine which filters (their names) need to be set
                # for specifying in order to select correct context for the
                # target view.
                for filter_name in visuals.info_params(info_key):
                    filter_object = visuals.get_filter(filter_name)
                    # Get the list of URI vars to be set for that filter
                    new_vars = filter_object.variable_settings(row)
                    url_vars += new_vars

        # See get_link_filter_names() comment for details
        for src_key, dst_key in visuals.get_link_filter_names(view, datasource['infos'],
                                                              datasource.get('link_filters', {})):
            try:
                url_vars += visuals.get_filter(src_key).variable_settings(row)
            except KeyError:
                pass

            try:
                url_vars += visuals.get_filter(dst_key).variable_settings(row)
            except KeyError:
                pass

        add_site_hint = visuals.may_add_site_hint(
            view_name,
            info_keys=datasource["infos"],
            single_info_keys=view["single_infos"],
            filter_names=dict(url_vars).keys())
        if add_site_hint and row.get('site'):
            url_vars.append(('site', row['site']))

        do = html.request.var("display_options")
        if do:
            url_vars.append(("display_options", do))

        filename = "mobile_view.py" if html.mobile else "view.py"
        return filename + "?" + html.urlencode_vars([("view_name", view_name)] + url_vars)


def get_host_tags(row):
    if isinstance(row.get("host_custom_variables"), dict):
        return row["host_custom_variables"].get("TAGS", "")

    if not isinstance(row.get("host_custom_variable_names"), list):
        return ""

    for name, val in zip(row["host_custom_variable_names"], row["host_custom_variable_values"]):
        if name == "TAGS":
            return val
    return ""


def get_graph_timerange_from_painter_options():
    value = painter_options.get("pnp_timerange")
    vs = painter_options.get_valuespec_of("pnp_timerange")
    return map(int, vs.compute_range(value)[0])


def paint_age(timestamp, has_been_checked, bold_if_younger_than, mode=None, what='past'):
    if not has_been_checked:
        return "age", "-"

    if mode is None:
        mode = painter_options.get("ts_format")

    if mode == "epoch":
        return "", str(int(timestamp))

    if mode == "both":
        css, h1 = paint_age(timestamp, has_been_checked, bold_if_younger_than, "abs", what=what)
        css, h2 = paint_age(timestamp, has_been_checked, bold_if_younger_than, "rel", what=what)
        return css, "%s - %s" % (h1, h2)

    dateformat = painter_options.get("ts_date")
    age = time.time() - timestamp
    if mode == "abs" or \
        (mode == "mixed" and abs(age) >= 48 * 3600):
        return "age", time.strftime(dateformat + " %H:%M:%S", time.localtime(timestamp))

    warn_txt = ''
    output_format = "%s"
    if what == 'future' and age > 0:
        warn_txt = ' <b>%s</b>' % _('in the past!')
    elif what == 'past' and age < 0:
        warn_txt = ' <b>%s</b>' % _('in the future!')
    elif what == 'both' and age > 0:
        output_format = "%%s %s" % _("ago")

    # Time delta less than two days => make relative time
    if age < 0:
        age = -age
        prefix = "in "
    else:
        prefix = ""
    if age < bold_if_younger_than:
        age_class = "age recent"
    else:
        age_class = "age"

    return age_class, prefix + (output_format % cmk.utils.render.approx_age(age)) + warn_txt


def paint_nagiosflag(row, field, bold_if_nonzero):
    value = row[field]
    yesno = {True: _("yes"), False: _("no")}[value != 0]
    if (value != 0) == bold_if_nonzero:
        return "badflag", yesno
    return "goodflag", yesno


def declare_simple_sorter(name, title, column, func):
    multisite_sorters[name] = {
        "title": title,
        "columns": [column],
        "cmp": lambda r1, r2: func(column, r1, r2)
    }


def declare_1to1_sorter(painter_name, func, col_num=0, reverse=False):
    multisite_sorters[painter_name] = {
        "title": multisite_painters[painter_name]['title'],
        "columns": multisite_painters[painter_name]['columns'],
    }
    if not reverse:
        multisite_sorters[painter_name]["cmp"] = \
            lambda r1, r2: func(multisite_painters[painter_name]['columns'][col_num], r1, r2)
    else:
        multisite_sorters[painter_name]["cmp"] = \
            lambda r1, r2: func(multisite_painters[painter_name]['columns'][col_num], r2, r1)
    return painter_name


def cmp_simple_number(column, r1, r2):
    return cmp(r1.get(column), r2.get(column))


def cmp_num_split(column, r1, r2):
    return cmk.gui.utils.cmp_num_split(r1[column].lower(), r2[column].lower())


def cmp_simple_string(column, r1, r2):
    v1, v2 = r1.get(column, ''), r2.get(column, '')
    return cmp_insensitive_string(v1, v2)


def cmp_insensitive_string(v1, v2):
    c = cmp(v1.lower(), v2.lower())
    # force a strict order in case of equal spelling but different
    # case!
    if c == 0:
        return cmp(v1, v2)
    return c


def cmp_string_list(column, r1, r2):
    v1 = ''.join(r1.get(column, []))
    v2 = ''.join(r2.get(column, []))
    return cmp_insensitive_string(v1, v2)


def cmp_service_name_equiv(r):
    if r == "Check_MK":
        return -6
    elif r == "Check_MK Agent":
        return -5
    elif r == "Check_MK Discovery":
        return -4
    elif r == "Check_MK inventory":
        return -3  # FIXME: Remove old name one day
    elif r == "Check_MK HW/SW Inventory":
        return -2
    return 0


def cmp_custom_variable(r1, r2, key, cmp_func):
    return cmp(get_custom_var(r1, key), get_custom_var(r2, key))


def cmp_ip_address(column, r1, r2):
    def split_ip(ip):
        try:
            return tuple(int(part) for part in ip.split('.'))
        except:
            return ip

    v1, v2 = split_ip(r1.get(column, '')), split_ip(r2.get(column, ''))
    return cmp(v1, v2)


def get_custom_var(row, key):
    for name, val in zip(row["custom_variable_names"], row["custom_variable_values"]):
        if name == key:
            return val
    return ""


def get_perfdata_nth_value(row, n, remove_unit=False):
    perfdata = row.get("service_perf_data")
    if not perfdata:
        return ''
    try:
        parts = perfdata.split()
        if len(parts) <= n:
            return ""  # too few values in perfdata
        _varname, rest = parts[n].split("=")
        number = rest.split(';')[0]
        # Remove unit. Why should we? In case of sorter (numeric)
        if remove_unit:
            while len(number) > 0 and not number[-1].isdigit():
                number = number[:-1]
        return number
    except Exception as e:
        return str(e)


# Get the definition of a tag group
# TODO: Refactor to caching object
_taggroups_by_id = {}


def get_tag_group(tgid):
    # Build a cache
    if not _taggroups_by_id:
        for entry in config.host_tag_groups():
            _taggroups_by_id[entry[0]] = (entry[1], entry[2])

    return _taggroups_by_id.get(tgid, (_("N/A"), []))


# Retrieve data via livestatus, convert into list of dicts,
# prepare row-function needed for painters
# datasource: the datasource object as defined in plugins/views/datasources.py
# columns: the list of livestatus columns to query
# add_columns: list of columns the datasource is known to add itself
#  (couldn't we get rid of this parameter by looking that up ourselves?)
# add_headers: additional livestatus headers to add
# only_sites: list of sites the query is limited to
# limit: maximum number of data rows to query
def query_data(datasource,
               columns,
               add_columns,
               add_headers,
               only_sites=None,
               limit=None,
               tablename=None):
    if only_sites is None:
        only_sites = []

    if tablename is None:
        tablename = datasource["table"]

    add_headers += datasource.get("add_headers", "")
    merge_column = datasource.get("merge_by")
    if merge_column:
        columns = [merge_column] + columns

    # Most layouts need current state of object in order to
    # choose background color - even if no painter for state
    # is selected. Make sure those columns are fetched. This
    # must not be done for the table 'log' as it cannot correctly
    # distinguish between service_state and host_state
    if "log" not in datasource["infos"]:
        state_columns = []
        if "service" in datasource["infos"]:
            state_columns += ["service_has_been_checked", "service_state"]
        if "host" in datasource["infos"]:
            state_columns += ["host_has_been_checked", "host_state"]
        for c in state_columns:
            if c not in columns:
                columns.append(c)

    auth_domain = datasource.get("auth_domain", "read")

    # Remove columns which are implicitely added by the datasource
    columns = [c for c in columns if c not in add_columns]
    query = "GET %s\n" % tablename
    rows = do_query_data(query, columns, add_columns, merge_column, add_headers, only_sites, limit,
                         auth_domain)

    # Datasource may have optional post processing function to filter out rows
    post_process_func = datasource.get("post_process")
    if post_process_func:
        return post_process_func(rows)
    return rows


def do_query_data(query, columns, add_columns, merge_column, add_headers, only_sites, limit,
                  auth_domain):
    query += "Columns: %s\n" % " ".join(columns)
    query += add_headers
    sites.live().set_prepend_site(True)

    if limit is not None:
        sites.live().set_limit(limit + 1)  # + 1: We need to know, if limit is exceeded
    else:
        sites.live().set_limit(None)

    if config.debug_livestatus_queries \
            and html.output_format == "html" and display_options.enabled(display_options.W):
        html.open_div(class_=["livestatus", "message"])
        html.tt(query.replace('\n', '<br>\n'))
        html.close_div()

    if only_sites:
        sites.live().set_only_sites(only_sites)
    sites.live().set_auth_domain(auth_domain)
    data = sites.live().query(query)
    sites.live().set_auth_domain("read")
    sites.live().set_only_sites(None)
    sites.live().set_prepend_site(False)
    sites.live().set_limit()  # removes limit

    if merge_column:
        data = _merge_data(data, columns)

    # convert lists-rows into dictionaries.
    # performance, but makes live much easier later.
    columns = ["site"] + columns + add_columns
    rows = [dict(zip(columns, row)) for row in data]

    return rows


# Merge all data rows with different sites but the same value
# in merge_column. We require that all column names are prefixed
# with the tablename. The column with the merge key is required
# to be the *second* column (right after the site column)
def _merge_data(data, columns):
    merged = {}
    mergefuncs = [lambda a, b: ""]  # site column is not merged

    def worst_service_state(a, b):
        if a == 2 or b == 2:
            return 2
        return max(a, b)

    def worst_host_state(a, b):
        if a == 1 or b == 1:
            return 1
        return max(a, b)

    for c in columns:
        _tablename, col = c.split("_", 1)
        if col.startswith("num_") or col.startswith("members"):
            mergefunc = lambda a, b: a + b
        elif col.startswith("worst_service"):
            return worst_service_state
        elif col.startswith("worst_host"):
            return worst_host_state
        else:
            mergefunc = lambda a, b: a
        mergefuncs.append(mergefunc)

    for row in data:
        mergekey = row[1]
        if mergekey in merged:
            oldrow = merged[mergekey]
            merged[mergekey] = [f(a, b) for f, a, b in zip(mergefuncs, oldrow, row)]
        else:
            merged[mergekey] = row

    # return all rows sorted according to merge key
    mergekeys = merged.keys()
    mergekeys.sort()
    return [merged[k] for k in mergekeys]


def join_row(row, cell):
    if isinstance(cell, JoinCell):
        return row.get("JOIN", {}).get(cell.join_service())
    return row


def get_view_infos(view):
    """Return list of available datasources (used to render filters)"""
    ds_name = view.get('datasource', html.request.var('datasource'))
    return multisite_datasources[ds_name]['infos']


def replace_action_url_macros(url, what, row):
    macros = {
        "HOSTNAME": row['host_name'],
        "HOSTADDRESS": row['host_address'],
        "USER_ID": config.user.id,
    }
    if what == 'service':
        macros.update({
            "SERVICEDESC": row['service_description'],
        })

    for key, val in macros.items():
        url = url.replace("$%s$" % key, val)
        url = url.replace("$%s_URL_ENCODED$" % key, html.urlencode(val))

    return url


# Intelligent Links to PNP4Nagios 0.6.X
def pnp_url(row, what, how='graph'):
    sitename = row["site"]
    host = cmk.utils.pnp_cleanup(row["host_name"])
    if what == "host":
        svc = "_HOST_"
    else:
        svc = cmk.utils.pnp_cleanup(row["service_description"])
    url_prefix = config.site(sitename)["url_prefix"]
    if html.mobile:
        url = url_prefix + ("pnp4nagios/index.php?kohana_uri=/mobile/%s/%s/%s" % \
            (how, html.urlencode(host), html.urlencode(svc)))
    else:
        url = url_prefix + ("pnp4nagios/index.php/%s?host=%s&srv=%s" % \
            (how, html.urlencode(host), html.urlencode(svc)))

    pnp_theme = html.get_theme()
    if pnp_theme == "classic":
        pnp_theme = "multisite"

    if how == 'graph':
        url += "&theme=%s&baseurl=%scheck_mk/" % (pnp_theme, html.urlencode(url_prefix))
    return url


def render_cache_info(what, row):
    cached_at = row["service_cached_at"]
    cache_interval = row["service_cache_interval"]
    cache_age = time.time() - cached_at

    text = _("Cache generated %s ago, cache interval: %s") % \
            (cmk.utils.render.approx_age(cache_age), cmk.utils.render.approx_age(cache_interval))

    if cache_interval:
        percentage = 100.0 * cache_age / cache_interval
        text += _(", elapsed cache lifespan: %s") % cmk.utils.render.percent(percentage)

    return text


def load_all_views():
    # Skip views which do not belong to known datasources
    all_views = visuals.load(
        'views',
        multisite_builtin_views,
        skip_func=lambda v: v['datasource'] not in multisite_datasources)
    return _transform_old_views(all_views)


def get_permitted_views(all_views):
    return visuals.available('views', all_views)


# Convert views that are saved in the pre 1.2.6-style
# FIXME: Can be removed one day. Mark as incompatible change or similar.
def _transform_old_views(all_views):
    for view in all_views.values():
        ds_name = view['datasource']
        datasource = multisite_datasources[ds_name]

        if "context" not in view:  # legacy views did not have this explicitly
            view.setdefault("user_sortable", True)

        if 'context_type' in view:
            # This code transforms views from user_views.mk which have been migrated with
            # daily snapshots from 2014-08 till beginning 2014-10.
            visuals.transform_old_visual(view)

        elif 'single_infos' not in view:
            # This tries to map the datasource and additional settings of the
            # views to get the correct view context
            #
            # This code transforms views from views.mk (legacy format) to the current format
            try:
                hide_filters = view.get('hide_filters')

                if 'service' in hide_filters and 'host' in hide_filters:
                    view['single_infos'] = ['service', 'host']
                elif 'service' in hide_filters and 'host' not in hide_filters:
                    view['single_infos'] = ['service']
                elif 'host' in hide_filters:
                    view['single_infos'] = ['host']
                elif 'hostgroup' in hide_filters:
                    view['single_infos'] = ['hostgroup']
                elif 'servicegroup' in hide_filters:
                    view['single_infos'] = ['servicegroup']
                elif 'aggr_service' in hide_filters:
                    view['single_infos'] = ['service']
                elif 'aggr_name' in hide_filters:
                    view['single_infos'] = ['aggr']
                elif 'aggr_group' in hide_filters:
                    view['single_infos'] = ['aggr_group']
                elif 'log_contact_name' in hide_filters:
                    view['single_infos'] = ['contact']
                elif 'event_host' in hide_filters:
                    view['single_infos'] = ['host']
                elif hide_filters == ['event_id', 'history_line']:
                    view['single_infos'] = ['history']
                elif 'event_id' in hide_filters:
                    view['single_infos'] = ['event']
                elif 'aggr_hosts' in hide_filters:
                    view['single_infos'] = ['host']
                else:
                    # For all other context types assume the view is showing multiple objects
                    # and the datasource can simply be gathered from the datasource
                    view['single_infos'] = []
            except:  # Exceptions can happen for views saved with certain GIT versions
                if config.debug:
                    raise

        # Convert from show_filters, hide_filters, hard_filters and hard_filtervars
        # to context construct
        if 'context' not in view:
            view[
                'show_filters'] = view['hide_filters'] + view['hard_filters'] + view['show_filters']

            single_keys = visuals.get_single_info_keys(view)

            # First get vars for the classic filters
            context = {}
            filtervars = dict(view['hard_filtervars'])
            all_vars = {}
            for filter_name in view['show_filters']:
                if filter_name in single_keys:
                    continue  # skip conflictings vars / filters

                context.setdefault(filter_name, {})
                try:
                    f = visuals.get_filter(filter_name)
                except:
                    # The exact match filters have been removed. They where used only as
                    # link filters anyway - at least by the builtin views.
                    continue

                for var in f.htmlvars:
                    # Check whether or not the filter is supported by the datasource,
                    # then either skip or use the filter vars
                    if var in filtervars and f.info in datasource['infos']:
                        value = filtervars[var]
                        all_vars[var] = value
                        context[filter_name][var] = value

                # We changed different filters since the visuals-rewrite. This must be treated here, since
                # we need to transform views which have been created with the old filter var names.
                # Changes which have been made so far:
                changed_filter_vars = {
                    'serviceregex': { # Name of the filter
                        # old var name: new var name
                        'service': 'service_regex',
                    },
                    'hostregex': {
                        'host': 'host_regex',
                    },
                    'hostgroupnameregex': {
                        'hostgroup_name': 'hostgroup_regex',
                    },
                    'servicegroupnameregex': {
                        'servicegroup_name': 'servicegroup_regex',
                    },
                    'opthostgroup': {
                        'opthostgroup': 'opthost_group',
                        'neg_opthostgroup': 'neg_opthost_group',
                    },
                    'optservicegroup': {
                        'optservicegroup': 'optservice_group',
                        'neg_optservicegroup': 'neg_optservice_group',
                    },
                    'hostgroup': {
                        'hostgroup': 'host_group',
                        'neg_hostgroup': 'neg_host_group',
                    },
                    'servicegroup': {
                        'servicegroup': 'service_group',
                        'neg_servicegroup': 'neg_service_group',
                    },
                    'host_contactgroup': {
                        'host_contactgroup': 'host_contact_group',
                        'neg_host_contactgroup': 'neg_host_contact_group',
                    },
                    'service_contactgroup': {
                        'service_contactgroup': 'service_contact_group',
                        'neg_service_contactgroup': 'neg_service_contact_group',
                    },
                }

                if filter_name in changed_filter_vars and f.info in datasource['infos']:
                    for old_var, new_var in changed_filter_vars[filter_name].items():
                        if old_var in filtervars:
                            value = filtervars[old_var]
                            all_vars[new_var] = value
                            context[filter_name][new_var] = value

            # Now, when there are single object infos specified, add these keys to the
            # context
            for single_key in single_keys:
                if single_key in all_vars:
                    context[single_key] = all_vars[single_key]

            view['context'] = context

        # Cleanup unused attributes
        for k in ['hide_filters', 'hard_filters', 'show_filters', 'hard_filtervars']:
            try:
                del view[k]
            except KeyError:
                pass

    return all_views


#.
#   .--Cells---------------------------------------------------------------.
#   |                           ____     _ _                               |
#   |                          / ___|___| | |___                           |
#   |                         | |   / _ \ | / __|                          |
#   |                         | |__|  __/ | \__ \                          |
#   |                          \____\___|_|_|___/                          |
#   |                                                                      |
#   +----------------------------------------------------------------------+
#   | View cell handling classes. Each cell instanciates a multisite       |
#   | painter to render a table cell.                                      |
#   '----------------------------------------------------------------------'


# A cell is an instance of a painter in a view (-> a cell or a grouping cell)
class Cell(object):
    # Wanted to have the "parse painter spec logic" in one place (The Cell() class)
    # but this should be cleaned up more. TODO: Move this to another place
    @staticmethod
    def painter_exists(painter_spec):
        if isinstance(painter_spec[0], tuple):
            painter_name = painter_spec[0][0]
        else:
            painter_name = painter_spec[0]

        return painter_name in multisite_painters

    # Wanted to have the "parse painter spec logic" in one place (The Cell() class)
    # but this should be cleaned up more. TODO: Move this to another place
    @staticmethod
    def is_join_cell(painter_spec):
        return len(painter_spec) >= 4

    def __init__(self, view, painter_spec=None):
        self._view = view
        self._painter_name = None
        self._painter_params = None
        self._link_view_name = None
        self._tooltip_painter_name = None

        if painter_spec:
            self._from_view(painter_spec)

    # In views the painters are saved as tuples of the following formats:
    #
    # Painter name, Link view name
    # ('service_discovery_service', None),
    #
    # Painter name,  Link view name, Hover painter name
    # ('host_plugin_output', None, None),
    #
    # Join column: Painter name, Link view name, hover painter name, Join service description
    # ('service_description', None, None, u'CPU load')
    #
    # Join column: Painter name, Link view name, hover painter name, Join service description, custom title
    # ('service_description', None, None, u'CPU load')
    #
    # Parameterized painters:
    # Same as above but instead of the "Painter name" a two element tuple with the painter name as
    # first element and a dictionary of parameters as second element is set.
    def _from_view(self, painter_spec):
        if isinstance(painter_spec[0], tuple):
            self._painter_name, self._painter_params = painter_spec[0]
        else:
            self._painter_name = painter_spec[0]

        if painter_spec[1] is not None:
            self._link_view_name = painter_spec[1]

        # Clean this call to Cell.painter_exists() up!
        if len(painter_spec) >= 3 and Cell.painter_exists((painter_spec[2], None)):
            self._tooltip_painter_name = painter_spec[2]

    # Get a list of columns we need to fetch in order to render this cell
    def needed_columns(self):
        columns = set(get_painter_columns(self.painter()))

        if self._link_view_name:
            if self._has_link():
                link_view = self._link_view()
                if link_view:
                    # TODO: Clean this up here
                    for filt in [
                            visuals.get_filter(fn) for fn in visuals.get_single_info_keys(link_view)
                    ]:
                        columns.update(filt.link_columns)

        if self.has_tooltip():
            columns.update(get_painter_columns(self.tooltip_painter()))

        return columns

    def is_joined(self):
        return False

    def join_service(self):
        return None

    def _has_link(self):
        return self._link_view_name is not None

    def _link_view(self):
        try:
            return get_permitted_views(load_all_views())[self._link_view_name]
        except KeyError:
            return None

    def painter(self):
        return multisite_painters[self._painter_name]

    def painter_name(self):
        return self._painter_name

    def export_title(self):
        return self._painter_name

    def painter_options(self):
        return self.painter().get("options", [])

    # The parameters configured in the view for this painter. In case the
    # painter has params, it defaults to the valuespec default value and
    # in case the painter has no params, it returns None.
    def painter_parameters(self):
        vs_painter_params = get_painter_params_valuespec(self.painter())
        if not vs_painter_params:
            return

        if vs_painter_params and self._painter_params is None:
            return vs_painter_params.default_value()
        return self._painter_params

    def title(self, use_short=True):
        painter = self.painter()
        if use_short:
            return self._get_short_title(painter)
        return self._get_long_title(painter)

    def _get_short_title(self, painter):
        if isinstance(painter.get("short"), (types.FunctionType, types.MethodType)):
            return painter["short"](self.painter_parameters())
        return painter.get("short", self._get_long_title(painter))

    def _get_long_title(self, painter):
        if isinstance(painter.get("title"), (types.FunctionType, types.MethodType)):
            return painter["title"](self.painter_parameters())
        return painter["title"]

    # Can either be:
    # True       : Is printable in PDF
    # False      : Is not printable at all
    # "<string>" : ID of a painter_printer (Reporting module)
    def printable(self):
        return self.painter().get("printable", True)

    def has_tooltip(self):
        return self._tooltip_painter_name is not None

    def tooltip_painter_name(self):
        return self._tooltip_painter_name

    def tooltip_painter(self):
        return multisite_painters[self._tooltip_painter_name]

    def paint_as_header(self, is_last_column_header=False):
        # Optional: Sort link in title cell
        # Use explicit defined sorter or implicit the sorter with the painter name
        # Important for links:
        # - Add the display options (Keeping the same display options as current)
        # - Link to _self (Always link to the current frame)
        classes = []
        onclick = ''
        title = ''
        if display_options.enabled(display_options.L) \
           and self._view.get('user_sortable', False) \
           and get_sorter_name_of_painter(self.painter_name()) is not None:
            params = [
                ('sort', self._sort_url()),
            ]
            if display_options.title_options:
                params.append(('display_options', display_options.title_options))

            classes += ["sort", get_primary_sorter_order(self._view, self.painter_name())]
            onclick = "location.href=\'%s\'" % html.makeuri(params, 'sort')
            title = _('Sort by %s') % self.title()

        if is_last_column_header:
            classes.append("last_col")

        html.open_th(class_=classes, onclick=onclick, title=title)
        html.write(self.title())
        html.close_th()
        #html.guitest_record_output("view", ("header", title))

    def _sort_url(self):
        """
        The following sorters need to be handled in this order:

        1. group by sorter (needed in grouped views)
        2. user defined sorters (url sorter)
        3. configured view sorters
        """
        sorter = []

        group_sort, user_sort, view_sort = get_separated_sorters(self._view)

        sorter = group_sort + user_sort + view_sort

        # Now apply the sorter of the current column:
        # - Negate/Disable when at first position
        # - Move to the first position when already in sorters
        # - Add in the front of the user sorters when not set
        sorter_name = get_sorter_name_of_painter(self.painter_name())
        if self.is_joined():
            # TODO: Clean this up and then remove Cell.join_service()
            this_asc_sorter = (sorter_name, False, self.join_service())
            this_desc_sorter = (sorter_name, True, self.join_service())
        else:
            this_asc_sorter = (sorter_name, False)
            this_desc_sorter = (sorter_name, True)

        if user_sort and this_asc_sorter == user_sort[0]:
            # Second click: Change from asc to desc order
            sorter[sorter.index(this_asc_sorter)] = this_desc_sorter

        elif user_sort and this_desc_sorter == user_sort[0]:
            # Third click: Remove this sorter
            sorter.remove(this_desc_sorter)

        else:
            # First click: add this sorter as primary user sorter
            # Maybe the sorter is already in the user sorters or view sorters, remove it
            for s in [user_sort, view_sort]:
                if this_asc_sorter in s:
                    s.remove(this_asc_sorter)
                if this_desc_sorter in s:
                    s.remove(this_desc_sorter)
            # Now add the sorter as primary user sorter
            sorter = group_sort + [this_asc_sorter] + user_sort + view_sort

        p = []
        for s in sorter:
            if len(s) == 2:
                p.append((s[1] and '-' or '') + s[0])
            else:
                p.append((s[1] and '-' or '') + s[0] + '~' + s[2])

        return ','.join(p)

    def render(self, row):
        row = join_row(row, self)

        try:
            tdclass, content = self.render_content(row)
        except:
            logger.exception("Failed to render painter '%s' (Row: %r)" % (self._painter_name, row))
            raise

        if tdclass is None:
            tdclass = ""

        if tdclass == "" and content == "":
            return "", ""

        # Add the optional link to another view
        if content and self._has_link():
            content = link_to_view(content, row, self._link_view_name)

        # Add the optional mouseover tooltip
        if content and self.has_tooltip():
            tooltip_cell = Cell(self._view, (self.tooltip_painter_name(), None))
            _tooltip_tdclass, tooltip_content = tooltip_cell.render_content(row)
            tooltip_text = html.strip_tags(tooltip_content)
            content = '<span title="%s">%s</span>' % (tooltip_text, content)

        return tdclass, content

    # Same as self.render() for HTML output: Gets a painter and a data
    # row and creates the text for being painted.
    def render_for_pdf(self, row, time_range):
        # TODO: Move this somewhere else!
        def find_htdocs_image_path(filename):
            dirs = [
                cmk.utils.paths.local_web_dir + "/htdocs/",
                cmk.utils.paths.web_dir + "/htdocs/",
            ]
            for d in dirs:
                if os.path.exists(d + filename):
                    return d + filename

        try:
            row = join_row(row, self)
            css_classes, txt = self.render_content(row)
            if txt is None:
                return css_classes, ""
            txt = txt.strip()

            # Handle <img...>. Our PDF writer cannot draw arbitrary
            # images, but all that we need for showing simple icons.
            # Current limitation: *one* image
            if txt.lower().startswith("<img"):
                img_filename = re.sub('.*src=["\']([^\'"]*)["\'].*', "\\1", str(txt))
                img_path = find_htdocs_image_path(img_filename)
                if img_path:
                    txt = ("icon", img_path)
                else:
                    txt = img_filename

            if isinstance(txt, HTML):
                txt = html.strip_tags("%s" % txt)

            elif not isinstance(txt, tuple):
                txt = html.escaper.unescape_attributes(txt)
                txt = html.strip_tags(txt)

            return css_classes, txt
        except Exception:
            raise MKGeneralException(
                'Failed to paint "%s": %s' % (self.painter_name(), traceback.format_exc()))

    def render_content(self, row):
        if not row:
            return "", ""  # nothing to paint

        painter = self.painter()
        paint_func = painter["paint"]

        # Painters can request to get the cell object handed over.
        # Detect that and give the painter this argument.
        arg_names = inspect.getargspec(paint_func)[0]
        painter_args = []
        for arg_name in arg_names:
            if arg_name == "row":
                painter_args.append(row)
            elif arg_name == "cell":
                painter_args.append(self)

        # Add optional painter arguments from painter specification
        if "args" in painter:
            painter_args += painter["args"]

        return painter["paint"](*painter_args)

    def paint(self, row, tdattrs="", is_last_cell=False):
        tdclass, content = self.render(row)
        has_content = content != ""

        if is_last_cell:
            if tdclass is None:
                tdclass = "last_col"
            else:
                tdclass += " last_col"

        if tdclass:
            html.write("<td %s class=\"%s\">" % (tdattrs, tdclass))
            html.write(content)
            html.close_td()
        else:
            html.write("<td %s>" % (tdattrs))
            html.write(content)
            html.close_td()
        #html.guitest_record_output("view", ("cell", content))

        return has_content


class JoinCell(Cell):
    def __init__(self, view, painter_spec):
        self._join_service_descr = None
        self._custom_title = None
        super(JoinCell, self).__init__(view, painter_spec)

    def _from_view(self, painter_spec):
        super(JoinCell, self)._from_view(painter_spec)

        if len(painter_spec) >= 4:
            self._join_service_descr = painter_spec[3]

        if len(painter_spec) == 5:
            self._custom_title = painter_spec[4]

    def is_joined(self):
        return True

    def join_service(self):
        return self._join_service_descr

    def livestatus_filter(self, join_column_name):
        return "Filter: %s = %s" % \
            (livestatus.lqencode(join_column_name), livestatus.lqencode(self._join_service_descr))

    def title(self, use_short=True):
        if self._custom_title:
            return self._custom_title
        return self._join_service_descr

    def export_title(self):
        return "%s.%s" % (self._painter_name, self.join_service())


class EmptyCell(Cell):
    def render(self, row):
        return "", ""

    def paint(self, row, tdattrs="", is_last_cell=False):
        return False


def get_cells(view):
    cells = []
    for e in view["painters"]:
        if not Cell.painter_exists(e):
            continue

        if Cell.is_join_cell(e):
            cells.append(JoinCell(view, e))

        else:
            cells.append(Cell(view, e))

    return cells


def get_group_cells(view):
    return [Cell(view, e) for e in view["group_painters"] if Cell.painter_exists(e)]


def output_csv_headers(view):
    filename = '%s-%s.csv' % (view['name'],
                              time.strftime('%Y-%m-%d_%H-%M-%S', time.localtime(time.time())))
    if isinstance(filename, unicode):
        filename = filename.encode("utf-8")
    html.response.headers["Content-Disposition"] = "Attachment; filename=\"%s\"" % filename


def get_sorter_name_of_painter(painter_name):
    painter = multisite_painters[painter_name]
    if 'sorter' in painter:
        return painter['sorter']

    elif painter_name in multisite_sorters:
        return painter_name


def get_separated_sorters(view):
    group_sort = [(get_sorter_name_of_painter(p[0]), False)
                  for p in view['group_painters']
                  if p[0] in multisite_painters and get_sorter_name_of_painter(p[0]) is not None]
    view_sort = [s for s in view['sorters'] if not s[0] in group_sort]

    # Get current url individual sorters. Parse the "sort" url parameter,
    # then remove the group sorters. The left sorters must be the user
    # individual sorters for this view.
    # Then remove the user sorters from the view sorters
    user_sort = parse_url_sorters(html.request.var('sort'))

    substract_sorters(user_sort, group_sort)
    substract_sorters(view_sort, user_sort)

    return group_sort, user_sort, view_sort


def get_primary_sorter_order(view, painter_name):
    sorter_name = get_sorter_name_of_painter(painter_name)
    this_asc_sorter = (sorter_name, False)
    this_desc_sorter = (sorter_name, True)
    _group_sort, user_sort, _view_sort = get_separated_sorters(view)
    if user_sort and this_asc_sorter == user_sort[0]:
        return 'asc'
    elif user_sort and this_desc_sorter == user_sort[0]:
        return 'desc'
    return ''


def parse_url_sorters(sort):
    sorters = []
    if not sort:
        return sorters
    for s in sort.split(','):
        if not '~' in s:
            sorters.append((s.replace('-', ''), s.startswith('-')))
        else:
            sorter, join_index = s.split('~', 1)
            sorters.append((sorter.replace('-', ''), sorter.startswith('-'), join_index))
    return sorters


def substract_sorters(base, remove):
    for s in remove:
        if s in base:
            base.remove(s)
        elif (s[0], not s[1]) in base:
            base.remove((s[0], not s[1]))


def get_painter_params_valuespec(painter):
    """Returns either the valuespec of the painter parameters or None"""
    if "params" not in painter:
        return

    if isinstance(painter["params"], (types.FunctionType, types.MethodType)):
        return painter["params"]()
    return painter["params"]
