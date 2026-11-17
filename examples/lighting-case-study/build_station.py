"""Extracted local lighting generator with an offline click-priority correction.

Comments were translated to English. The obsolete, disabled Computer Link branch
and project-specific wheel loading were omitted. See README.md for provenance.
"""
from fx3u.dsl import Prog

M_PER_BUTTON = 10
T_PER_BUTTON = 2
D_PER_BUTTON = 1


def fx_addr(prefix, index):
    group, position = divmod(index, 8)
    return "%s%03d" % (prefix, group * 10 + position)


class _ZoneGuard:
    """Reject duplicate owners and unqualified cross-kind output arbitration."""

    def __init__(self):
        self._claimed = {'toggle': {}, 'long_off': {}}

    def claim(self, kind, y_name, prog_name, button_index):
        if any(y_name in owners for other, owners in self._claimed.items() if other != kind):
            raise ValueError(
                'OFF/TOGGLE mapping conflict for zone %s (station %s): '
                'central command priority is not implemented' % (y_name, prog_name))
        owners = self._claimed[kind]
        if y_name in owners:
            raise ValueError(
                'Mapping collision (%s): zone %s (station %s) already assigned '
                'to button %02d, also requested by button %02d '
                '— review *_ZONE_OVERRIDES in config.py'
                % (kind, y_name, prog_name, owners[y_name], button_index))
        owners[y_name] = button_index


def _add_command_diag(p, all_cmds, cmd_seq_reg, cmd_last_reg):
    """Latch the latest one-scan command and increment the event counter for polling."""
    codes = [('single', 1), ('double', 2), ('triple', 3), ('long', 4)]
    with p.module('cmddiag'):
        p.dev('cmd_seq', cmd_seq_reg, 'DIAG command count')
        p.dev('cmd_last', cmd_last_reg, 'DIAG latest: button*10+type')
        for i, cmds in all_cmds:
            for key, code in codes:
                bit = cmds[key]
                p.LD(bit).INC('cmd_seq')
                p.LD(bit).MOV('K%d' % (i * 10 + code), 'cmd_last')


def build_station(prog_name, n_buttons, window, long_press,
                   single_zones_for, double_zones_for, long_off_zones_for,
                   auto_off_hours_for=None,
                   enable_cmd_diag=True, cmd_seq_reg='D100', cmd_last_reg='D101'):
    """Build one local station; no communication-port initialization or deployment."""
    # Materialize and validate every mapping before allocating or emitting IL.
    # Python bools and negative indices must never select a physical output.
    zone_maps = []
    for i in range(n_buttons):
        mappings = []
        for kind, zones_for in [('single', single_zones_for),
                                ('double', double_zones_for),
                                ('long_off', long_off_zones_for)]:
            zones = list(zones_for(prog_name, i))
            for zone in zones:
                if type(zone) is not int or not 0 <= zone < n_buttons:
                    raise ValueError(
                        'Invalid zone index %r for %s (station %s, button %02d): '
                        'expected int in [0, %d)' %
                        (zone, kind, prog_name, i, n_buttons))
            mappings.append(zones)
        zone_maps.append(mappings)

    p = Prog()
    x_names = ['x%02d' % i for i in range(n_buttons)]
    y_names = ['y%02d' % i for i in range(n_buttons)]
    for i in range(n_buttons):
        p.dev(x_names[i], fx_addr('X', i), 'Button %02d (physical input)' % i)
        p.dev(y_names[i], fx_addr('Y', i), 'Zone %02d (placeholder)' % i)

    guard = _ZoneGuard()
    all_cmds = []
    for i in range(n_buttons):
        btn_name = 'btn%02d' % i
        m_base, t_base, d_base = i * M_PER_BUTTON, i * T_PER_BUTTON, i * D_PER_BUTTON
        with p.module(btn_name,
                      M=(m_base, m_base + M_PER_BUTTON - 1),
                      T=(t_base, t_base + T_PER_BUTTON - 1),
                      D=(d_base, d_base + D_PER_BUTTON - 1)):
            p.alloc(btn_name, 'M', 'Button %02d (intermediate)' % i)
            p.in_remap(btn_name, x_names[i])
            cmds = p.click_button(btn_name, win=window, long_press=long_press)
            all_cmds.append((i, cmds))

            # Single and double share one OUT ownership namespace.
            for z in zone_maps[i][0]:
                guard.claim('toggle', y_names[z], prog_name, i)
                p.toggle(y_names[z], cmds['single'])
            for z in zone_maps[i][1]:
                guard.claim('toggle', y_names[z], prog_name, i)
                p.toggle(y_names[z], cmds['double'])
            for z in zone_maps[i][2]:
                guard.claim('long_off', y_names[z], prog_name, i)
                p.force_off(y_names[z], cmds['long'])


    # Triple commands remain unbound, but all four types reach diagnostics.
    # These registers preserve the latest event, not an event queue.
    if enable_cmd_diag:
        _add_command_diag(p, all_cmds, cmd_seq_reg, cmd_last_reg)

    if auto_off_hours_for is not None:
        with p.module('autooff', C=(0, n_buttons - 1)):
            for i in range(n_buttons):
                hours = auto_off_hours_for(prog_name, i)
                if hours is not None:
                    p.auto_off(y_names[i], hours * 60)

    p.END()
    return p, y_names

