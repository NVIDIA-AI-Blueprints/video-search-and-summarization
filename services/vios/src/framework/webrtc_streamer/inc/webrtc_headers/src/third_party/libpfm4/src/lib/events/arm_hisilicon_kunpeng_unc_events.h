/*
 * Copyright (c) 2021 Barcelona Supercomputing Center
 * Contributed by Estanislao Mercadal Melià <lau.mercadal@bsc.es>
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
 * of the Software, and to permit persons to whom the Software is furnished to do so,
 * subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
 * INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
 * PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
 * HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
 * CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
 * OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 *
 * Hisilicon Kunpeng 920
 * Based on https://developer.arm.com/documentation/ddi0487/latest/ and
 * https://github.com/torvalds/linux/blob/master/tools/perf/pmu-events/arch/arm64/hisilicon/hip08/uncore-ddrc.json
 * https://github.com/torvalds/linux/blob/master/tools/perf/pmu-events/arch/arm64/hisilicon/hip08/uncore-hha.json
 * https://github.com/torvalds/linux/blob/master/tools/perf/pmu-events/arch/arm64/hisilicon/hip08/uncore-l3c.json
 */

static const arm_entry_t arm_kunpeng_unc_ddrc_pe[ ] = {
	{
		.name = "flux_wr",
		.code = 0x00,
		.desc = "DDRC total write operations."
	},
	{
		.name = "flux_rd",
		.code = 0x01,
		.desc = "DDRC total read operations."
	},
	{
		.name = "flux_wcmd",
		.code = 0x02,
		.desc = "DDRC write commands."
	},
	{
		.name = "flux_rcmd",
		.code = 0x03,
		.desc = "DDRC read commands."
	},
	{
		.name = "pre_cmd",
		.code = 0x04,
		.desc = "DDRC precharge commands."
	},
	{
		.name = "act_cmd",
		.code = 0x05,
		.desc = "DDRC active commands."
	},
	{
		.name = "rnk_chg",
		.code = 0x06,
		.desc = "DDRC rank commands."
	},
	{
		.name = "rw_chg",
		.code = 0x07,
		.desc = "DDRC read and write changes."
	}
};

static const arm_entry_t arm_kunpeng_unc_hha_pe[ ] = {
	{
		.name = "rx_ops_num",
		.code = 0x00,
		.desc = "The number of all operations received by the HHA."
	},
	{
		.name = "rx_outer",
		.code = 0x01,
		.desc = "The number of all operations received by the HHA from another socket."
	},
	{
		.name = "rx_sccl",
		.code = 0x02,
		.desc = "The number of all operations received by the HHA from another SCCL in this socket."
	},
	{
		.name = "rx_ccix",
		.code = 0x03,
		.desc = "Count of the number of operations that HHA has received from CCIX."
	},
	{
		.name = "rd_ddr_64b",
		.code = 0x1c,
		.desc = "The number of read operations sent by HHA to DDRC which size is 64bytes."
	},
	{
		.name = "wr_ddr_64b",
		.code = 0x1d,
		.desc = "The number of write operations sent by HHA to DDRC which size is 64 bytes."
	},
	{
		.name = "rd_ddr_128b",
		.code = 0x1e,
		.desc = "The number of read operations sent by HHA to DDRC which size is 128 bytes."
	},
	{
		.name = "wr_ddr_128b",
		.code = 0x1f,
		.desc = "The number of write operations sent by HHA to DDRC which size is 128 bytes."
	},
	{
		.name = "spill_num",
		.code = 0x20,
		.desc = "Count of the number of spill operations that the HHA has sent."
	},
	{
		.name = "spill_success",
		.code = 0x21,
		.desc = "Count of the number of successful spill operations that the HHA has sent."
	}
};

static const arm_entry_t arm_kunpeng_unc_l3c_pe[ ] = {
	{
		.name = "rd_cpipe",
		.code = 0x00,
		.desc = "Total read accesses."
	},
	{
		.name = "wr_cpipe",
		.code = 0x01,
		.desc = "Total write accesses."
	},
	{
		.name = "rd_hit_cpipe",
		.code = 0x02,
		.desc = "Total read hits."
	},
	{
		.name = "wr_hit_cpipe",
		.code = 0x03,
		.desc = "Total write hits."
	},
	{
		.name = "victim_num",
		.code = 0x04,
		.desc = "l3c precharge commands."
	},
	{
		.name = "rd_spipe",
		.code = 0x20,
		.desc = "Count of the number of read lines that come from this cluster of CPU core in spipe."
	},
	{
		.name = "wr_spipe",
		.code = 0x21,
		.desc = "Count of the number of write lines that come from this cluster of CPU core in spipe."
	},
	{
		.name = "rd_hit_spipe",
		.code = 0x22,
		.desc = "Count of the number of read lines that hits in spipe of this L3C."
	},
	{
		.name = "wr_hit_spipe",
		.code = 0x23,
		.desc = "Count of the number of write lines that hits in spipe of this L3C."
	},
	{
		.name = "back_invalid",
		.code = 0x29,
		.desc = "Count of the number of L3C back invalid operations."
	},
	{
		.name = "retry_cpu",
		.code = 0x40,
		.desc = "Count of the number of retry that L3C suppresses the CPU operations."
	},
	{
		.name = "retry_ring",
		.code = 0x41,
		.desc = "Count of the number of retry that L3C suppresses the ring operations."
	},
	{
		.name = "prefetch_drop",
		.code = 0x42,
		.desc = "Count of the number of prefetch drops from this L3C."
	}
};
