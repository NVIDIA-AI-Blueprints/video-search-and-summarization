#ifndef __POWERPC_NEST_EVENTS_H__
#define __POWERPC_NEST_EVENTS_H__

#define POWERPC_PME_NEST_MCS_00 0
#define POWERPC_PME_NEST_MCS_01 1
#define POWERPC_PME_NEST_MCS_02 2
#define POWERPC_PME_NEST_MCS_03 3

static const pme_power_entry_t powerpc_nest_read_pe[] = {
    [ POWERPC_PME_NEST_MCS_00 ] = {
        .pme_name = "MCS_00",
        .pme_code = 0x118,
        .pme_short_desc = "Total Read Bandwidth seen on both MCS of MC0",
        .pme_long_desc = "Total Read Bandwidth seen on both MCS of MC0",
    },
    [ POWERPC_PME_NEST_MCS_01 ] = {
        .pme_name = "MCS_01",
        .pme_code = 0x120,
        .pme_short_desc = "Total Read Bandwidth seen on both MCS of MC1",
        .pme_long_desc = "Total Read Bandwidth seen on both MCS of MC1",
    },
    [ POWERPC_PME_NEST_MCS_02 ] = {
        .pme_name = "MCS_02",
        .pme_code = 0x128,
        .pme_short_desc = "Total Read Bandwidth seen on both MCS of MC2",
        .pme_long_desc = "Total Read Bandwidth seen on both MCS of MC2",
    },
    [ POWERPC_PME_NEST_MCS_03 ] = {
        .pme_name = "MCS_03",
        .pme_code = 0x130,
        .pme_short_desc = "Total Read Bandwidth seen on both MCS of MC3",
        .pme_long_desc = "Total Read Bandwidth seen on both MCS of MC3",
    },
};

static const pme_power_entry_t powerpc_nest_write_pe[] = {
    [ POWERPC_PME_NEST_MCS_00 ] = {
        .pme_name = "MCS_00",
        .pme_code = 0x198,
        .pme_short_desc = "Total Write Bandwidth seen on both MCS of MC0",
        .pme_long_desc = "Total Write Bandwidth seen on both MCS of MC0",
    },
    [ POWERPC_PME_NEST_MCS_01 ] = {
        .pme_name = "MCS_01",
        .pme_code = 0x1a0,
        .pme_short_desc = "Total Write Bandwidth seen on both MCS of MC1",
        .pme_long_desc = "Total Write Bandwidth seen on both MCS of MC1",
    },
    [ POWERPC_PME_NEST_MCS_02 ] = {
        .pme_name = "MCS_02",
        .pme_code = 0x1a8,
        .pme_short_desc = "Total Write Bandwidth seen on both MCS of MC2",
        .pme_long_desc = "Total Write Bandwidth seen on both MCS of MC2",
    },
    [ POWERPC_PME_NEST_MCS_03 ] = {
        .pme_name = "MCS_03",
        .pme_code = 0x1b0,
        .pme_short_desc = "Total Write Bandwidth seen on both MCS of MC3",
        .pme_long_desc = "Total Write Bandwidth seen on both MCS of MC3",
    },
};
#endif
