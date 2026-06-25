struct cpuinfo_mock_file filesystem[] = {
	{.path = "/proc/cpuinfo",
	 .size = 3717,
	 .content =
		 "processor\t: 0\n"
		 "BogoMIPS\t: 49.15\n"
		 "Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm jscvt fcma lrcpc dcpop sha3 sm3 sm4 asimddp sha512 sve asimdfhm dit uscat ilrcpc flagm ssbs sb paca pacg dcpodp sve2 sveaes svepmull svebitperm svesha3 svesm4 flagm2 frint svei8mm svebf16 i8mm bti\n"
		 "CPU implementer\t: 0x41\n"
		 "CPU architecture: 8\n"
		 "CPU variant\t: 0x1\n"
		 "CPU part\t: 0xd46\n"
		 "CPU revision\t: 1\n"
		 "\n"
		 "processor\t: 1\n"
		 "BogoMIPS\t: 49.15\n"
		 "Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm jscvt fcma lrcpc dcpop sha3 sm3 sm4 asimddp sha512 sve asimdfhm dit uscat ilrcpc flagm ssbs sb paca pacg dcpodp sve2 sveaes svepmull svebitperm svesha3 svesm4 flagm2 frint svei8mm svebf16 i8mm bti\n"
		 "CPU implementer\t: 0x41\n"
		 "CPU architecture: 8\n"
		 "CPU variant\t: 0x1\n"
		 "CPU part\t: 0xd46\n"
		 "CPU revision\t: 1\n"
		 "\n"
		 "processor\t: 2\n"
		 "BogoMIPS\t: 49.15\n"
		 "Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm jscvt fcma lrcpc dcpop sha3 sm3 sm4 asimddp sha512 sve asimdfhm dit uscat ilrcpc flagm ssbs sb paca pacg dcpodp sve2 sveaes svepmull svebitperm svesha3 svesm4 flagm2 frint svei8mm svebf16 i8mm bti\n"
		 "CPU implementer\t: 0x41\n"
		 "CPU architecture: 8\n"
		 "CPU variant\t: 0x1\n"
		 "CPU part\t: 0xd46\n"
		 "CPU revision\t: 1\n"
		 "\n"
		 "processor\t: 3\n"
		 "BogoMIPS\t: 49.15\n"
		 "Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm jscvt fcma lrcpc dcpop sha3 sm3 sm4 asimddp sha512 sve asimdfhm dit uscat ilrcpc flagm ssbs sb paca pacg dcpodp sve2 sveaes svepmull svebitperm svesha3 svesm4 flagm2 frint svei8mm svebf16 i8mm bti\n"
		 "CPU implementer\t: 0x41\n"
		 "CPU architecture: 8\n"
		 "CPU variant\t: 0x1\n"
		 "CPU part\t: 0xd46\n"
		 "CPU revision\t: 1\n"
		 "\n"
		 "processor\t: 4\n"
		 "BogoMIPS\t: 49.15\n"
		 "Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm jscvt fcma lrcpc dcpop sha3 sm3 sm4 asimddp sha512 sve asimdfhm dit uscat ilrcpc flagm ssbs sb paca pacg dcpodp sve2 sveaes svepmull svebitperm svesha3 svesm4 flagm2 frint svei8mm svebf16 i8mm bti\n"
		 "CPU implementer\t: 0x41\n"
		 "CPU architecture: 8\n"
		 "CPU variant\t: 0x1\n"
		 "CPU part\t: 0xd4d\n"
		 "CPU revision\t: 0\n"
		 "\n"
		 "processor\t: 5\n"
		 "BogoMIPS\t: 49.15\n"
		 "Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm jscvt fcma lrcpc dcpop sha3 sm3 sm4 asimddp sha512 sve asimdfhm dit uscat ilrcpc flagm ssbs sb paca pacg dcpodp sve2 sveaes svepmull svebitperm svesha3 svesm4 flagm2 frint svei8mm svebf16 i8mm bti\n"
		 "CPU implementer\t: 0x41\n"
		 "CPU architecture: 8\n"
		 "CPU variant\t: 0x1\n"
		 "CPU part\t: 0xd4d\n"
		 "CPU revision\t: 0\n"
		 "\n"
		 "processor\t: 6\n"
		 "BogoMIPS\t: 49.15\n"
		 "Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm jscvt fcma lrcpc dcpop sha3 sm3 sm4 asimddp sha512 sve asimdfhm dit uscat ilrcpc flagm ssbs sb paca pacg dcpodp sve2 sveaes svepmull svebitperm svesha3 svesm4 flagm2 frint svei8mm svebf16 i8mm bti\n"
		 "CPU implementer\t: 0x41\n"
		 "CPU architecture: 8\n"
		 "CPU variant\t: 0x1\n"
		 "CPU part\t: 0xd4d\n"
		 "CPU revision\t: 0\n"
		 "\n"
		 "processor\t: 7\n"
		 "BogoMIPS\t: 49.15\n"
		 "Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm jscvt fcma lrcpc dcpop sha3 sm3 sm4 asimddp sha512 sve asimdfhm dit uscat ilrcpc flagm ssbs sb paca pacg dcpodp sve2 sveaes svepmull svebitperm svesha3 svesm4 flagm2 frint svei8mm svebf16 i8mm bti\n"
		 "CPU implementer\t: 0x41\n"
		 "CPU architecture: 8\n"
		 "CPU variant\t: 0x1\n"
		 "CPU part\t: 0xd4d\n"
		 "CPU revision\t: 0\n"
		 "\n"
		 "processor\t: 8\n"
		 "BogoMIPS\t: 49.15\n"
		 "Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm jscvt fcma lrcpc dcpop sha3 sm3 sm4 asimddp sha512 sve asimdfhm dit uscat ilrcpc flagm ssbs sb paca pacg dcpodp sve2 sveaes svepmull svebitperm svesha3 svesm4 flagm2 frint svei8mm svebf16 i8mm bti\n"
		 "CPU implementer\t: 0x41\n"
		 "CPU architecture: 8\n"
		 "CPU variant\t: 0x1\n"
		 "CPU part\t: 0xd4e\n"
		 "CPU revision\t: 0\n"},
	{.path = "/sys/devices/system/cpu/kernel_max", .size = 3, .content = "31\n"},
	{.path = "/sys/devices/system/cpu/possible", .size = 4, .content = "0-8\n"},
	{.path = "/sys/devices/system/cpu/present", .size = 4, .content = "0-8\n"},
	{.path = "/sys/devices/system/cpu/online", .size = 4, .content = "0-8\n"},
	{.path = "/sys/devices/system/cpu/offline", .size = 1, .content = "\n"},
	{.path = "/sys/devices/system/cpu/modalias",
	 .size = 251,
	 .content =
		 "cpu:type:aarch64:feature:,0000,0001,0002,0003,0004,0005,0006,0007,0008,0009,000A,000B,000C,000D,000E,000F,0010,0011,0012,0013,0014,0015,0016,0017,0018,0019,001A,001B,001C,001D,001E,001F,0020,0021,0022,0023,0024,0025,0026,0027,0028,0029,002C,002D,0031\n"},
	{.path = "/sys/devices/system/cpu/cpuidle/current_driver", .size = 10, .content = "psci_idle\n"},
	{.path = "/sys/devices/system/cpu/cpuidle/current_governor_ro", .size = 4, .content = "teo\n"},
	{.path = "/sys/devices/system/cpu/cpu0/cpuidle/driver/name", .size = 10, .content = "psci_idle\n"},
	{.path = "/sys/devices/system/cpu/cpu0/cpufreq/affected_cpus", .size = 8, .content = "0 1 2 3\n"},
	{.path = "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", .size = 8, .content = "1704000\n"},
	{.path = "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_min_freq", .size = 7, .content = "324000\n"},
	{.path = "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_transition_latency", .size = 8, .content = "5000000\n"},
	{.path = "/sys/devices/system/cpu/cpu0/cpufreq/related_cpus", .size = 8, .content = "0 1 2 3\n"},
	{.path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies",
	 .size = 76,
	 .content = "324000 610000 820000 955000 1098000 1197000 1328000 1425000 1548000 1704000 \n"},
	{.path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors",
	 .size = 57,
	 .content = "sched_pixel conservative powersave performance schedutil \n"},
	{.path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", .size = 8, .content = "1197000\n"},
	{.path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver", .size = 15, .content = "exynos_cpufreq\n"},
	{.path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", .size = 12, .content = "sched_pixel\n"},
	{.path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq", .size = 7, .content = "324000\n"},
	{.path = "/sys/devices/system/cpu/cpu0/cpufreq/stats/time_in_state",
	 .size = 131,
	 .content = "324000 1026376\n"
		    "610000 18555\n"
		    "820000 12889\n"
		    "955000 3943\n"
		    "1098000 4202\n"
		    "1197000 2559\n"
		    "1328000 4948\n"
		    "1425000 1421\n"
		    "1548000 1288\n"
		    "1704000 9901\n"},
	{.path = "/sys/devices/system/cpu/cpu0/cpufreq/stats/total_trans", .size = 7, .content = "115798\n"},
	{.path = "/sys/devices/system/cpu/cpu0/topology/core_id", .size = 2, .content = "0\n"},
	{.path = "/sys/devices/system/cpu/cpu0/topology/core_siblings_list", .size = 2, .content = "0\n"},
	{.path = "/sys/devices/system/cpu/cpu0/topology/cluster_cpus", .size = 4, .content = "00f\n"},
	{.path = "/sys/devices/system/cpu/cpu0/topology/cluster_cpus_list", .size = 4, .content = "0-3\n"},
	{.path = "/sys/devices/system/cpu/cpu0/topology/physical_package_id", .size = 2, .content = "0\n"},
	{.path = "/sys/devices/system/cpu/cpu0/topology/thread_siblings", .size = 4, .content = "001\n"},
	{.path = "/sys/devices/system/cpu/cpu0/topology/thread_siblings_list", .size = 2, .content = "0\n"},
	{.path = "/sys/devices/system/cpu/cpu1/cpuidle/driver/name", .size = 10, .content = "psci_idle\n"},
	{.path = "/sys/devices/system/cpu/cpu1/cpufreq/affected_cpus", .size = 8, .content = "0 1 2 3\n"},
	{.path = "/sys/devices/system/cpu/cpu1/cpufreq/cpuinfo_max_freq", .size = 8, .content = "1704000\n"},
	{.path = "/sys/devices/system/cpu/cpu1/cpufreq/cpuinfo_min_freq", .size = 7, .content = "324000\n"},
	{.path = "/sys/devices/system/cpu/cpu1/cpufreq/cpuinfo_transition_latency", .size = 8, .content = "5000000\n"},
	{.path = "/sys/devices/system/cpu/cpu1/cpufreq/related_cpus", .size = 8, .content = "0 1 2 3\n"},
	{.path = "/sys/devices/system/cpu/cpu1/cpufreq/scaling_available_frequencies",
	 .size = 76,
	 .content = "324000 610000 820000 955000 1098000 1197000 1328000 1425000 1548000 1704000 \n"},
	{.path = "/sys/devices/system/cpu/cpu1/cpufreq/scaling_available_governors",
	 .size = 57,
	 .content = "sched_pixel conservative powersave performance schedutil \n"},
	{.path = "/sys/devices/system/cpu/cpu1/cpufreq/scaling_cur_freq", .size = 7, .content = "324000\n"},
	{.path = "/sys/devices/system/cpu/cpu1/cpufreq/scaling_driver", .size = 15, .content = "exynos_cpufreq\n"},
	{.path = "/sys/devices/system/cpu/cpu1/cpufreq/scaling_governor", .size = 12, .content = "sched_pixel\n"},
	{.path = "/sys/devices/system/cpu/cpu1/cpufreq/scaling_min_freq", .size = 7, .content = "324000\n"},
	{.path = "/sys/devices/system/cpu/cpu1/cpufreq/stats/time_in_state",
	 .size = 131,
	 .content = "324000 1027068\n"
		    "610000 18568\n"
		    "820000 12892\n"
		    "955000 3945\n"
		    "1098000 4212\n"
		    "1197000 2563\n"
		    "1328000 4950\n"
		    "1425000 1422\n"
		    "1548000 1288\n"
		    "1704000 9910\n"},
	{.path = "/sys/devices/system/cpu/cpu1/cpufreq/stats/total_trans", .size = 7, .content = "115912\n"},
	{.path = "/sys/devices/system/cpu/cpu1/topology/core_id", .size = 2, .content = "1\n"},
	{.path = "/sys/devices/system/cpu/cpu1/topology/core_siblings_list", .size = 2, .content = "1\n"},
	{.path = "/sys/devices/system/cpu/cpu1/topology/cluster_cpus", .size = 4, .content = "00f\n"},
	{.path = "/sys/devices/system/cpu/cpu1/topology/cluster_cpus_list", .size = 4, .content = "0-3\n"},
	{.path = "/sys/devices/system/cpu/cpu1/topology/physical_package_id", .size = 2, .content = "0\n"},
	{.path = "/sys/devices/system/cpu/cpu1/topology/thread_siblings", .size = 4, .content = "002\n"},
	{.path = "/sys/devices/system/cpu/cpu1/topology/thread_siblings_list", .size = 2, .content = "1\n"},
	{.path = "/sys/devices/system/cpu/cpu2/cpuidle/driver/name", .size = 10, .content = "psci_idle\n"},
	{.path = "/sys/devices/system/cpu/cpu2/cpufreq/affected_cpus", .size = 8, .content = "0 1 2 3\n"},
	{.path = "/sys/devices/system/cpu/cpu2/cpufreq/cpuinfo_max_freq", .size = 8, .content = "1704000\n"},
	{.path = "/sys/devices/system/cpu/cpu2/cpufreq/cpuinfo_min_freq", .size = 7, .content = "324000\n"},
	{.path = "/sys/devices/system/cpu/cpu2/cpufreq/cpuinfo_transition_latency", .size = 8, .content = "5000000\n"},
	{.path = "/sys/devices/system/cpu/cpu2/cpufreq/related_cpus", .size = 8, .content = "0 1 2 3\n"},
	{.path = "/sys/devices/system/cpu/cpu2/cpufreq/scaling_available_frequencies",
	 .size = 76,
	 .content = "324000 610000 820000 955000 1098000 1197000 1328000 1425000 1548000 1704000 \n"},
	{.path = "/sys/devices/system/cpu/cpu2/cpufreq/scaling_available_governors",
	 .size = 57,
	 .content = "sched_pixel conservative powersave performance schedutil \n"},
	{.path = "/sys/devices/system/cpu/cpu2/cpufreq/scaling_cur_freq", .size = 7, .content = "324000\n"},
	{.path = "/sys/devices/system/cpu/cpu2/cpufreq/scaling_driver", .size = 15, .content = "exynos_cpufreq\n"},
	{.path = "/sys/devices/system/cpu/cpu2/cpufreq/scaling_governor", .size = 12, .content = "sched_pixel\n"},
	{.path = "/sys/devices/system/cpu/cpu2/cpufreq/scaling_min_freq", .size = 7, .content = "324000\n"},
	{.path = "/sys/devices/system/cpu/cpu2/cpufreq/stats/time_in_state",
	 .size = 131,
	 .content = "324000 1027745\n"
		    "610000 18580\n"
		    "820000 12909\n"
		    "955000 3950\n"
		    "1098000 4217\n"
		    "1197000 2570\n"
		    "1328000 4958\n"
		    "1425000 1423\n"
		    "1548000 1290\n"
		    "1704000 9923\n"},
	{.path = "/sys/devices/system/cpu/cpu2/cpufreq/stats/total_trans", .size = 7, .content = "116054\n"},
	{.path = "/sys/devices/system/cpu/cpu2/topology/core_id", .size = 2, .content = "2\n"},
	{.path = "/sys/devices/system/cpu/cpu2/topology/core_siblings_list", .size = 2, .content = "2\n"},
	{.path = "/sys/devices/system/cpu/cpu2/topology/cluster_cpus", .size = 4, .content = "00f\n"},
	{.path = "/sys/devices/system/cpu/cpu2/topology/cluster_cpus_list", .size = 4, .content = "0-3\n"},
	{.path = "/sys/devices/system/cpu/cpu2/topology/physical_package_id", .size = 2, .content = "0\n"},
	{.path = "/sys/devices/system/cpu/cpu2/topology/thread_siblings", .size = 4, .content = "004\n"},
	{.path = "/sys/devices/system/cpu/cpu2/topology/thread_siblings_list", .size = 2, .content = "2\n"},
	{.path = "/sys/devices/system/cpu/cpu3/cpuidle/driver/name", .size = 10, .content = "psci_idle\n"},
	{.path = "/sys/devices/system/cpu/cpu3/cpufreq/affected_cpus", .size = 8, .content = "0 1 2 3\n"},
	{.path = "/sys/devices/system/cpu/cpu3/cpufreq/cpuinfo_max_freq", .size = 8, .content = "1704000\n"},
	{.path = "/sys/devices/system/cpu/cpu3/cpufreq/cpuinfo_min_freq", .size = 7, .content = "324000\n"},
	{.path = "/sys/devices/system/cpu/cpu3/cpufreq/cpuinfo_transition_latency", .size = 8, .content = "5000000\n"},
	{.path = "/sys/devices/system/cpu/cpu3/cpufreq/related_cpus", .size = 8, .content = "0 1 2 3\n"},
	{.path = "/sys/devices/system/cpu/cpu3/cpufreq/scaling_available_frequencies",
	 .size = 76,
	 .content = "324000 610000 820000 955000 1098000 1197000 1328000 1425000 1548000 1704000 \n"},
	{.path = "/sys/devices/system/cpu/cpu3/cpufreq/scaling_available_governors",
	 .size = 57,
	 .content = "sched_pixel conservative powersave performance schedutil \n"},
	{.path = "/sys/devices/system/cpu/cpu3/cpufreq/scaling_cur_freq", .size = 7, .content = "324000\n"},
	{.path = "/sys/devices/system/cpu/cpu3/cpufreq/scaling_driver", .size = 15, .content = "exynos_cpufreq\n"},
	{.path = "/sys/devices/system/cpu/cpu3/cpufreq/scaling_governor", .size = 12, .content = "sched_pixel\n"},
	{.path = "/sys/devices/system/cpu/cpu3/cpufreq/scaling_min_freq", .size = 7, .content = "324000\n"},
	{.path = "/sys/devices/system/cpu/cpu3/cpufreq/stats/time_in_state",
	 .size = 131,
	 .content = "324000 1028434\n"
		    "610000 18597\n"
		    "820000 12919\n"
		    "955000 3952\n"
		    "1098000 4223\n"
		    "1197000 2577\n"
		    "1328000 4960\n"
		    "1425000 1424\n"
		    "1548000 1290\n"
		    "1704000 9931\n"},
	{.path = "/sys/devices/system/cpu/cpu3/cpufreq/stats/total_trans", .size = 7, .content = "116188\n"},
	{.path = "/sys/devices/system/cpu/cpu3/topology/core_id", .size = 2, .content = "3\n"},
	{.path = "/sys/devices/system/cpu/cpu3/topology/core_siblings_list", .size = 2, .content = "3\n"},
	{.path = "/sys/devices/system/cpu/cpu3/topology/cluster_cpus", .size = 4, .content = "00f\n"},
	{.path = "/sys/devices/system/cpu/cpu3/topology/cluster_cpus_list", .size = 4, .content = "0-3\n"},
	{.path = "/sys/devices/system/cpu/cpu3/topology/physical_package_id", .size = 2, .content = "0\n"},
	{.path = "/sys/devices/system/cpu/cpu3/topology/thread_siblings", .size = 4, .content = "008\n"},
	{.path = "/sys/devices/system/cpu/cpu3/topology/thread_siblings_list", .size = 2, .content = "3\n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpuidle/driver/name", .size = 10, .content = "psci_idle\n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpufreq/affected_cpus", .size = 8, .content = "4 5 6 7\n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpufreq/cpuinfo_max_freq", .size = 8, .content = "2367000\n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpufreq/cpuinfo_min_freq", .size = 7, .content = "402000\n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpufreq/cpuinfo_transition_latency", .size = 8, .content = "5000000\n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpufreq/related_cpus", .size = 8, .content = "4 5 6 7\n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpufreq/scaling_available_frequencies",
	 .size = 115,
	 .content =
		 "402000 578000 697000 712000 910000 1065000 1221000 1328000 1418000 1572000 1836000 1945000 2130000 2245000 2367000 \n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpufreq/scaling_available_governors",
	 .size = 57,
	 .content = "sched_pixel conservative powersave performance schedutil \n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpufreq/scaling_cur_freq", .size = 8, .content = "2130000\n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpufreq/scaling_driver", .size = 15, .content = "exynos_cpufreq\n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpufreq/scaling_governor", .size = 12, .content = "sched_pixel\n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpufreq/scaling_max_freq", .size = 8, .content = "2367000\n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpufreq/scaling_min_freq", .size = 7, .content = "402000\n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpufreq/stats/time_in_state",
	 .size = 194,
	 .content = "402000 1023353\n"
		    "578000 3901\n"
		    "697000 2240\n"
		    "712000 282\n"
		    "910000 8163\n"
		    "1065000 2924\n"
		    "1221000 2351\n"
		    "1328000 1368\n"
		    "1418000 1272\n"
		    "1572000 6658\n"
		    "1836000 12747\n"
		    "1945000 4128\n"
		    "2130000 2880\n"
		    "2245000 1570\n"
		    "2367000 15230\n"},
	{.path = "/sys/devices/system/cpu/cpu4/cpufreq/stats/total_trans", .size = 6, .content = "62643\n"},
	{.path = "/sys/devices/system/cpu/cpu4/topology/core_id", .size = 2, .content = "0\n"},
	{.path = "/sys/devices/system/cpu/cpu4/topology/core_siblings_list", .size = 2, .content = "4\n"},
	{.path = "/sys/devices/system/cpu/cpu4/topology/cluster_cpus", .size = 4, .content = "0f0\n"},
	{.path = "/sys/devices/system/cpu/cpu4/topology/cluster_cpus_list", .size = 4, .content = "4-7\n"},
	{.path = "/sys/devices/system/cpu/cpu4/topology/physical_package_id", .size = 2, .content = "1\n"},
	{.path = "/sys/devices/system/cpu/cpu4/topology/thread_siblings", .size = 4, .content = "010\n"},
	{.path = "/sys/devices/system/cpu/cpu4/topology/thread_siblings_list", .size = 2, .content = "4\n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpuidle/driver/name", .size = 10, .content = "psci_idle\n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpufreq/affected_cpus", .size = 8, .content = "4 5 6 7\n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpufreq/cpuinfo_max_freq", .size = 8, .content = "2367000\n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpufreq/cpuinfo_min_freq", .size = 7, .content = "402000\n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpufreq/cpuinfo_transition_latency", .size = 8, .content = "5000000\n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpufreq/related_cpus", .size = 8, .content = "4 5 6 7\n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpufreq/scaling_available_frequencies",
	 .size = 115,
	 .content =
		 "402000 578000 697000 712000 910000 1065000 1221000 1328000 1418000 1572000 1836000 1945000 2130000 2245000 2367000 \n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpufreq/scaling_available_governors",
	 .size = 57,
	 .content = "sched_pixel conservative powersave performance schedutil \n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpufreq/scaling_cur_freq", .size = 8, .content = "1945000\n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpufreq/scaling_driver", .size = 15, .content = "exynos_cpufreq\n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpufreq/scaling_governor", .size = 12, .content = "sched_pixel\n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpufreq/scaling_max_freq", .size = 8, .content = "2367000\n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpufreq/scaling_min_freq", .size = 7, .content = "402000\n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpufreq/stats/time_in_state",
	 .size = 194,
	 .content = "402000 1023895\n"
		    "578000 3911\n"
		    "697000 2240\n"
		    "712000 282\n"
		    "910000 8163\n"
		    "1065000 2924\n"
		    "1221000 2355\n"
		    "1328000 1368\n"
		    "1418000 1272\n"
		    "1572000 6684\n"
		    "1836000 12823\n"
		    "1945000 4147\n"
		    "2130000 2894\n"
		    "2245000 1571\n"
		    "2367000 15301\n"},
	{.path = "/sys/devices/system/cpu/cpu5/cpufreq/stats/total_trans", .size = 6, .content = "62928\n"},
	{.path = "/sys/devices/system/cpu/cpu5/topology/core_id", .size = 2, .content = "1\n"},
	{.path = "/sys/devices/system/cpu/cpu5/topology/core_siblings_list", .size = 2, .content = "5\n"},
	{.path = "/sys/devices/system/cpu/cpu5/topology/cluster_cpus", .size = 4, .content = "0f0\n"},
	{.path = "/sys/devices/system/cpu/cpu5/topology/cluster_cpus_list", .size = 4, .content = "4-7\n"},
	{.path = "/sys/devices/system/cpu/cpu5/topology/physical_package_id", .size = 2, .content = "1\n"},
	{.path = "/sys/devices/system/cpu/cpu5/topology/thread_siblings", .size = 4, .content = "020\n"},
	{.path = "/sys/devices/system/cpu/cpu5/topology/thread_siblings_list", .size = 2, .content = "5\n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpuidle/driver/name", .size = 10, .content = "psci_idle\n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpufreq/affected_cpus", .size = 8, .content = "4 5 6 7\n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpufreq/cpuinfo_max_freq", .size = 8, .content = "2367000\n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpufreq/cpuinfo_min_freq", .size = 7, .content = "402000\n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpufreq/cpuinfo_transition_latency", .size = 8, .content = "5000000\n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpufreq/related_cpus", .size = 8, .content = "4 5 6 7\n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpufreq/scaling_available_frequencies",
	 .size = 115,
	 .content =
		 "402000 578000 697000 712000 910000 1065000 1221000 1328000 1418000 1572000 1836000 1945000 2130000 2245000 2367000 \n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpufreq/scaling_available_governors",
	 .size = 57,
	 .content = "sched_pixel conservative powersave performance schedutil \n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpufreq/scaling_cur_freq", .size = 8, .content = "1945000\n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpufreq/scaling_driver", .size = 15, .content = "exynos_cpufreq\n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpufreq/scaling_governor", .size = 12, .content = "sched_pixel\n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpufreq/scaling_max_freq", .size = 8, .content = "2367000\n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpufreq/scaling_min_freq", .size = 7, .content = "402000\n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpufreq/stats/time_in_state",
	 .size = 194,
	 .content = "402000 1024410\n"
		    "578000 3922\n"
		    "697000 2240\n"
		    "712000 282\n"
		    "910000 8163\n"
		    "1065000 2924\n"
		    "1221000 2355\n"
		    "1328000 1368\n"
		    "1418000 1277\n"
		    "1572000 6718\n"
		    "1836000 12892\n"
		    "1945000 4164\n"
		    "2130000 2901\n"
		    "2245000 1571\n"
		    "2367000 15400\n"},
	{.path = "/sys/devices/system/cpu/cpu6/cpufreq/stats/total_trans", .size = 6, .content = "63222\n"},
	{.path = "/sys/devices/system/cpu/cpu6/topology/core_id", .size = 2, .content = "2\n"},
	{.path = "/sys/devices/system/cpu/cpu6/topology/core_siblings_list", .size = 2, .content = "6\n"},
	{.path = "/sys/devices/system/cpu/cpu6/topology/cluster_cpus", .size = 4, .content = "0f0\n"},
	{.path = "/sys/devices/system/cpu/cpu6/topology/cluster_cpus_list", .size = 4, .content = "4-7\n"},
	{.path = "/sys/devices/system/cpu/cpu6/topology/physical_package_id", .size = 2, .content = "1\n"},
	{.path = "/sys/devices/system/cpu/cpu6/topology/thread_siblings", .size = 4, .content = "040\n"},
	{.path = "/sys/devices/system/cpu/cpu6/topology/thread_siblings_list", .size = 2, .content = "6\n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpuidle/driver/name", .size = 10, .content = "psci_idle\n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpufreq/affected_cpus", .size = 8, .content = "4 5 6 7\n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpufreq/cpuinfo_max_freq", .size = 8, .content = "2367000\n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpufreq/cpuinfo_min_freq", .size = 7, .content = "402000\n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpufreq/cpuinfo_transition_latency", .size = 8, .content = "5000000\n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpufreq/related_cpus", .size = 8, .content = "4 5 6 7\n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpufreq/scaling_available_frequencies",
	 .size = 115,
	 .content =
		 "402000 578000 697000 712000 910000 1065000 1221000 1328000 1418000 1572000 1836000 1945000 2130000 2245000 2367000 \n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpufreq/scaling_available_governors",
	 .size = 57,
	 .content = "sched_pixel conservative powersave performance schedutil \n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpufreq/scaling_cur_freq", .size = 8, .content = "2130000\n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpufreq/scaling_driver", .size = 15, .content = "exynos_cpufreq\n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpufreq/scaling_governor", .size = 12, .content = "sched_pixel\n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpufreq/scaling_max_freq", .size = 8, .content = "2367000\n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpufreq/scaling_min_freq", .size = 7, .content = "402000\n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpufreq/stats/time_in_state",
	 .size = 194,
	 .content = "402000 1024939\n"
		    "578000 3931\n"
		    "697000 2242\n"
		    "712000 282\n"
		    "910000 8170\n"
		    "1065000 2925\n"
		    "1221000 2355\n"
		    "1328000 1368\n"
		    "1418000 1277\n"
		    "1572000 6747\n"
		    "1836000 12967\n"
		    "1945000 4185\n"
		    "2130000 2918\n"
		    "2245000 1571\n"
		    "2367000 15468\n"},
	{.path = "/sys/devices/system/cpu/cpu7/cpufreq/stats/total_trans", .size = 6, .content = "63514\n"},
	{.path = "/sys/devices/system/cpu/cpu7/topology/core_id", .size = 2, .content = "3\n"},
	{.path = "/sys/devices/system/cpu/cpu7/topology/core_siblings_list", .size = 2, .content = "7\n"},
	{.path = "/sys/devices/system/cpu/cpu7/topology/cluster_cpus", .size = 4, .content = "0f0\n"},
	{.path = "/sys/devices/system/cpu/cpu7/topology/cluster_cpus_list", .size = 4, .content = "4-7\n"},
	{.path = "/sys/devices/system/cpu/cpu7/topology/physical_package_id", .size = 2, .content = "1\n"},
	{.path = "/sys/devices/system/cpu/cpu7/topology/thread_siblings", .size = 4, .content = "080\n"},
	{.path = "/sys/devices/system/cpu/cpu7/topology/thread_siblings_list", .size = 2, .content = "7\n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpuidle/driver/name", .size = 10, .content = "psci_idle\n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpufreq/affected_cpus", .size = 2, .content = "8\n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpufreq/cpuinfo_max_freq", .size = 8, .content = "2914000\n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpufreq/cpuinfo_min_freq", .size = 7, .content = "500000\n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpufreq/cpuinfo_transition_latency", .size = 8, .content = "5000000\n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpufreq/related_cpus", .size = 2, .content = "8\n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpufreq/scaling_available_frequencies",
	 .size = 115,
	 .content =
		 "500000 880000 1164000 1298000 1557000 1745000 1885000 2049000 2147000 2294000 2363000 2556000 2687000 2850000 2914000\n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpufreq/scaling_available_governors",
	 .size = 57,
	 .content = "sched_pixel conservative powersave performance schedutil \n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpufreq/scaling_cur_freq", .size = 7, .content = "500000\n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpufreq/scaling_driver", .size = 15, .content = "exynos_cpufreq\n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpufreq/scaling_governor", .size = 12, .content = "sched_pixel\n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpufreq/scaling_max_freq", .size = 8, .content = "2914000\n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpufreq/scaling_min_freq", .size = 7, .content = "500000\n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpufreq/stats/time_in_state",
	 .size = 188,
	 .content = "500000 1530909\n"
		    "880000 3209\n"
		    "1164000 1553\n"
		    "1298000 885\n"
		    "1557000 2004\n"
		    "1745000 1410\n"
		    "1885000 631\n"
		    "2049000 4090\n"
		    "2147000 278\n"
		    "2294000 522\n"
		    "2363000 228\n"
		    "2556000 525\n"
		    "2687000 311\n"
		    "2850000 940\n"
		    "2914000 3929\n"},
	{.path = "/sys/devices/system/cpu/cpu8/cpufreq/stats/total_trans", .size = 6, .content = "18185\n"},
	{.path = "/sys/devices/system/cpu/cpu8/topology/core_id", .size = 2, .content = "0\n"},
	{.path = "/sys/devices/system/cpu/cpu8/topology/core_siblings_list", .size = 2, .content = "8\n"},
	{.path = "/sys/devices/system/cpu/cpu8/topology/cluster_cpus", .size = 4, .content = "f00\n"},
	{.path = "/sys/devices/system/cpu/cpu8/topology/cluster_cpus_list", .size = 2, .content = "8\n"},
	{.path = "/sys/devices/system/cpu/cpu8/topology/physical_package_id", .size = 2, .content = "2\n"},
	{.path = "/sys/devices/system/cpu/cpu8/topology/thread_siblings", .size = 4, .content = "100\n"},
	{.path = "/sys/devices/system/cpu/cpu8/topology/thread_siblings_list", .size = 2, .content = "8\n"},
	{NULL},
};
#ifdef __ANDROID__
struct cpuinfo_mock_property properties[] = {
	{
		.key = "aaudio.hw_burst_min_usec",
		.value = "2000",
	},
	{
		.key = "aaudio.mmap_exclusive_policy",
		.value = "2",
	},
	{
		.key = "aaudio.mmap_policy",
		.value = "2",
	},
	{
		.key = "dalvik.vm.appimageformat",
		.value = "lz4",
	},
	{
		.key = "dalvik.vm.dex2oat-Xms",
		.value = "64m",
	},
	{
		.key = "dalvik.vm.dex2oat-Xmx",
		.value = "512m",
	},
	{
		.key = "dalvik.vm.dexopt.secondary",
		.value = "true",
	},
	{
		.key = "dalvik.vm.heapgrowthlimit",
		.value = "256m",
	},
	{
		.key = "dalvik.vm.heapmaxfree",
		.value = "32m",
	},
	{
		.key = "dalvik.vm.heapminfree",
		.value = "8m",
	},
	{
		.key = "dalvik.vm.heapsize",
		.value = "512m",
	},
	{
		.key = "dalvik.vm.heapstartsize",
		.value = "16m",
	},
	{
		.key = "dalvik.vm.heaptargetutilization",
		.value = "0.5",
	},
	{
		.key = "dalvik.vm.image-dex2oat-Xms",
		.value = "64m",
	},
	{
		.key = "dalvik.vm.image-dex2oat-Xmx",
		.value = "64m",
	},
	{
		.key = "dalvik.vm.isa.arm64.features",
		.value = "default",
	},
	{
		.key = "dalvik.vm.isa.arm64.variant",
		.value = "cortex-a55",
	},
	{
		.key = "dalvik.vm.usejit",
		.value = "true",
	},
	{
		.key = "debug.atrace.tags.enableflags",
		.value = "0",
	},
	{
		.key = "debug.force_rtl",
		.value = "false",
	},
	{
		.key = "dev.bootcomplete",
		.value = "1",
	},
	{
		.key = "drm.service.enabled",
		.value = "true",
	},
	{
		.key = "gsm.current.phone-type",
		.value = "1,1",
	},
	{
		.key = "gsm.network.type",
		.value = "IWLAN,IWLAN",
	},
	{
		.key = "gsm.operator.alpha",
		.value = ",",
	},
	{
		.key = "gsm.operator.iso-country",
		.value = "us,",
	},
	{
		.key = "gsm.operator.isroaming",
		.value = "false,false",
	},
	{
		.key = "gsm.operator.numeric",
		.value = ",",
	},
	{
		.key = "gsm.sim.operator.alpha",
		.value = ",",
	},
	{
		.key = "gsm.sim.operator.iso-country",
		.value = ",",
	},
	{
		.key = "gsm.sim.operator.numeric",
		.value = ",",
	},
	{
		.key = "gsm.sim.state",
		.value = "ABSENT,NOT_READY",
	},
	{
		.key = "gsm.version.baseband",
		.value = "g5300i-230927-231102-B-11040898,g5300i-230927-231102-B-11040898",
	},
	{
		.key = "gsm.version.ril-impl",
		.value = "Samsung S.LSI Vendor RIL V2.3 Build 2023-12-12 01:31:33",
	},
	{
		.key = "hwservicemanager.ready",
		.value = "true",
	},
	{
		.key = "init.svc.adbd",
		.value = "running",
	},
	{
		.key = "init.svc.audioserver",
		.value = "running",
	},
	{
		.key = "init.svc.bootanim",
		.value = "stopped",
	},
	{
		.key = "init.svc.cameraserver",
		.value = "running",
	},
	{
		.key = "init.svc.drm",
		.value = "running",
	},
	{
		.key = "init.svc.gatekeeperd",
		.value = "running",
	},
	{
		.key = "init.svc.gnss_service",
		.value = "running",
	},
	{
		.key = "init.svc.hidl_memory",
		.value = "running",
	},
	{
		.key = "init.svc.hwservicemanager",
		.value = "running",
	},
	{
		.key = "init.svc.init-radio-sh",
		.value = "stopped",
	},
	{
		.key = "init.svc.insmod_sh",
		.value = "stopped",
	},
	{
		.key = "init.svc.installd",
		.value = "running",
	},
	{
		.key = "init.svc.lmkd",
		.value = "running",
	},
	{
		.key = "init.svc.logd",
		.value = "running",
	},
	{
		.key = "init.svc.logd-reinit",
		.value = "stopped",
	},
	{
		.key = "init.svc.media",
		.value = "running",
	},
	{
		.key = "init.svc.mediadrm",
		.value = "running",
	},
	{
		.key = "init.svc.mediaextractor",
		.value = "running",
	},
	{
		.key = "init.svc.mediametrics",
		.value = "running",
	},
	{
		.key = "init.svc.netd",
		.value = "running",
	},
	{
		.key = "init.svc.nfc_hal_service",
		.value = "running",
	},
	{
		.key = "init.svc.ril-daemon",
		.value = "running",
	},
	{
		.key = "init.svc.servicemanager",
		.value = "running",
	},
	{
		.key = "init.svc.storaged",
		.value = "running",
	},
	{
		.key = "init.svc.surfaceflinger",
		.value = "running",
	},
	{
		.key = "init.svc.tombstoned",
		.value = "running",
	},
	{
		.key = "init.svc.ueventd",
		.value = "running",
	},
	{
		.key = "init.svc.update_engine",
		.value = "running",
	},
	{
		.key = "init.svc.update_verifier_nonencrypted",
		.value = "stopped",
	},
	{
		.key = "init.svc.vndservicemanager",
		.value = "running",
	},
	{
		.key = "init.svc.vold",
		.value = "running",
	},
	{
		.key = "init.svc.wificond",
		.value = "running",
	},
	{
		.key = "init.svc.wpa_supplicant",
		.value = "running",
	},
	{
		.key = "init.svc.zygote",
		.value = "running",
	},
	{
		.key = "keyguard.no_require_sim",
		.value = "true",
	},
	{
		.key = "media.mediadrmservice.enable",
		.value = "true",
	},
	{
		.key = "net.bt.name",
		.value = "Android",
	},
	{
		.key = "nfc.initialized",
		.value = "true",
	},
	{
		.key = "partition.system.verified",
		.value = "2",
	},
	{
		.key = "partition.vendor.verified",
		.value = "2",
	},
	{
		.key = "persist.sys.dalvik.vm.lib.2",
		.value = "libart.so",
	},
	{
		.key = "persist.sys.sf.color_saturation",
		.value = "1.0",
	},
	{
		.key = "persist.sys.timezone",
		.value = "America/Los_Angeles",
	},
	{
		.key = "persist.sys.usb.config",
		.value = "adb",
	},
	{
		.key = "pm.dexopt.ab-ota",
		.value = "speed-profile",
	},
	{
		.key = "pm.dexopt.bg-dexopt",
		.value = "speed-profile",
	},
	{
		.key = "pm.dexopt.first-boot",
		.value = "verify",
	},
	{
		.key = "pm.dexopt.inactive",
		.value = "verify",
	},
	{
		.key = "pm.dexopt.install",
		.value = "speed-profile",
	},
	{
		.key = "pm.dexopt.shared",
		.value = "speed",
	},
	{
		.key = "ro.adb.secure",
		.value = "1",
	},
	{
		.key = "ro.allow.mock.location",
		.value = "0",
	},
	{
		.key = "ro.atrace.core.services",
		.value = "com.google.android.gms,com.google.android.gms.ui,com.google.android.gms.persistent",
	},
	{
		.key = "ro.audio.monitorRotation",
		.value = "true",
	},
	{
		.key = "ro.baseband",
		.value = "unknown",
	},
	{
		.key = "ro.board.platform",
		.value = "zuma",
	},
	{
		.key = "ro.boot.avb_version",
		.value = "1.2",
	},
	{
		.key = "ro.boot.bootdevice",
		.value = "13200000.ufs",
	},
	{
		.key = "ro.boot.bootloader",
		.value = "ripcurrent-14.1-11012321",
	},
	{
		.key = "ro.boot.bootreason",
		.value = "reboot",
	},
	{
		.key = "ro.boot.boottime",
		.value =
			"0BLE:112,1BLL:7,1BLE:73,2BLL:28,2BLE:557,3BLL:314,3BLE:194,SW:10058,KL:13,KD:0,ODT:62,AVB:96,FWL:149",
	},
	{
		.key = "ro.boot.ddr_info",
		.value = "Micron",
	},
	{
		.key = "ro.boot.ddr_size",
		.value = "8GiB",
	},
	{
		.key = "ro.boot.flash.locked",
		.value = "0",
	},
	{
		.key = "ro.boot.hardware",
		.value = "shiba",
	},
	{
		.key = "ro.boot.hardware.color",
		.value = "HAZ",
	},
	{
		.key = "ro.boot.hardware.revision",
		.value = "MP1.0",
	},
	{
		.key = "ro.boot.hardware.sku",
		.value = "G9BQD",
	},
	{
		.key = "ro.boot.hardware.ufs",
		.value = "128GB,Micron",
	},
	{
		.key = "ro.boot.revision",
		.value = "MP1.0",
	},
	{
		.key = "ro.boot.serialno",
		.value = "39061FDJH005AB",
	},
	{
		.key = "ro.boot.slot_suffix",
		.value = "_a",
	},
	{
		.key = "ro.boot.vbmeta.avb_version",
		.value = "1.2",
	},
	{
		.key = "ro.boot.vbmeta.device_state",
		.value = "unlocked",
	},
	{
		.key = "ro.boot.vbmeta.digest",
		.value = "64384054dbd4c6c629c119818f784bf515a4a9996c14f3bb7b314a6267fc7706",
	},
	{
		.key = "ro.boot.vbmeta.hash_alg",
		.value = "sha256",
	},
	{
		.key = "ro.boot.vbmeta.size",
		.value = "16512",
	},
	{
		.key = "ro.boot.verifiedbootstate",
		.value = "orange",
	},
	{
		.key = "ro.boot.veritymode",
		.value = "enforcing",
	},
	{
		.key = "ro.boot.wificountrycode",
		.value = "00",
	},
	{
		.key = "ro.bootimage.build.date",
		.value = "Tue Dec 12 05:18:54 UTC 2023",
	},
	{
		.key = "ro.bootimage.build.date.utc",
		.value = "1702358334",
	},
	{
		.key = "ro.bootimage.build.fingerprint",
		.value = "google/shiba/shiba:14/UQ1A.240105.004/11206848:userdebug/dev-keys",
	},
	{
		.key = "ro.bootloader",
		.value = "ripcurrent-14.1-11012321",
	},
	{
		.key = "ro.bootmode",
		.value = "unknown",
	},
	{
		.key = "ro.build.ab_update",
		.value = "true",
	},
	{
		.key = "ro.build.characteristics",
		.value = "nosdcard",
	},
	{
		.key = "ro.build.date",
		.value = "Tue Dec 12 05:18:54 UTC 2023",
	},
	{
		.key = "ro.build.date.utc",
		.value = "1702358334",
	},
	{
		.key = "ro.build.description",
		.value = "shiba-userdebug 14 UQ1A.240105.004 11206848 dev-keys",
	},
	{
		.key = "ro.build.display.id",
		.value = "shiba-userdebug 14 UQ1A.240105.004 11206848 dev-keys",
	},
	{
		.key = "ro.build.expect.baseband",
		.value = "g5300i-230927-231102-B-11040898",
	},
	{
		.key = "ro.build.expect.bootloader",
		.value = "ripcurrent-14.1-11012321",
	},
	{
		.key = "ro.build.fingerprint",
		.value = "google/shiba/shiba:14/UQ1A.240105.004/11206848:userdebug/dev-keys",
	},
	{
		.key = "ro.build.flavor",
		.value = "shiba-userdebug",
	},
	{
		.key = "ro.build.host",
		.value = "abfarm-release-rbe-32-2004-00107",
	},
	{
		.key = "ro.build.id",
		.value = "UQ1A.240105.004",
	},
	{
		.key = "ro.build.product",
		.value = "shiba",
	},
	{
		.key = "ro.build.tags",
		.value = "dev-keys",
	},
	{
		.key = "ro.build.type",
		.value = "userdebug",
	},
	{
		.key = "ro.build.user",
		.value = "android-build",
	},
	{
		.key = "ro.build.version.all_codenames",
		.value = "REL",
	},
	{
		.key = "ro.build.version.codename",
		.value = "REL",
	},
	{
		.key = "ro.build.version.incremental",
		.value = "11206848",
	},
	{
		.key = "ro.build.version.preview_sdk",
		.value = "0",
	},
	{
		.key = "ro.build.version.release",
		.value = "14",
	},
	{
		.key = "ro.build.version.sdk",
		.value = "34",
	},
	{
		.key = "ro.build.version.security_patch",
		.value = "2024-01-05",
	},
	{
		.key = "ro.carrier",
		.value = "unknown",
	},
	{
		.key = "ro.com.android.dataroaming",
		.value = "false",
	},
	{
		.key = "ro.com.android.prov_mobiledata",
		.value = "false",
	},
	{
		.key = "ro.com.google.clientidbase",
		.value = "android-google",
	},
	{
		.key = "ro.com.google.ime.theme_id",
		.value = "5",
	},
	{
		.key = "ro.config.alarm_alert",
		.value = "Fresh_start.ogg",
	},
	{
		.key = "ro.config.media_vol_steps",
		.value = "25",
	},
	{
		.key = "ro.config.notification_sound",
		.value = "Eureka.ogg",
	},
	{
		.key = "ro.config.ringtone",
		.value = "Your_new_adventure.ogg",
	},
	{
		.key = "ro.config.vc_call_vol_steps",
		.value = "7",
	},
	{
		.key = "ro.control_privapp_permissions",
		.value = "enforce",
	},
	{
		.key = "ro.crypto.state",
		.value = "encrypted",
	},
	{
		.key = "ro.crypto.type",
		.value = "file",
	},
	{
		.key = "ro.dalvik.vm.native.bridge",
		.value = "0",
	},
	{
		.key = "ro.debuggable",
		.value = "1",
	},
	{
		.key = "ro.error.receiver.system.apps",
		.value = "com.google.android.gms",
	},
	{
		.key = "ro.frp.pst",
		.value = "/dev/block/by-name/frp",
	},
	{
		.key = "ro.hardware",
		.value = "shiba",
	},
	{
		.key = "ro.oem_unlock_supported",
		.value = "1",
	},
	{
		.key = "ro.opa.eligible_device",
		.value = "true",
	},
	{
		.key = "ro.opengles.version",
		.value = "196610",
	},
	{
		.key = "ro.product.board",
		.value = "shiba",
	},
	{
		.key = "ro.product.brand",
		.value = "google",
	},
	{
		.key = "ro.product.cpu.abi",
		.value = "arm64-v8a",
	},
	{
		.key = "ro.product.cpu.abilist",
		.value = "arm64-v8a",
	},
	{
		.key = "ro.product.cpu.abilist64",
		.value = "arm64-v8a",
	},
	{
		.key = "ro.product.device",
		.value = "shiba",
	},
	{
		.key = "ro.product.first_api_level",
		.value = "34",
	},
	{
		.key = "ro.product.locale",
		.value = "en-US",
	},
	{
		.key = "ro.product.manufacturer",
		.value = "Google",
	},
	{
		.key = "ro.product.model",
		.value = "Pixel 8",
	},
	{
		.key = "ro.product.name",
		.value = "shiba",
	},
	{
		.key = "ro.property_service.version",
		.value = "2",
	},
	{
		.key = "ro.revision",
		.value = "MP1.0",
	},
	{
		.key = "ro.secure",
		.value = "1",
	},
	{
		.key = "ro.serialno",
		.value = "39061FDJH005AB",
	},
	{
		.key = "ro.setupwizard.enterprise_mode",
		.value = "1",
	},
	{
		.key = "ro.setupwizard.esim_cid_ignore",
		.value = "00000001",
	},
	{
		.key = "ro.sf.lcd_density",
		.value = "420",
	},
	{
		.key = "ro.storage_manager.enabled",
		.value = "false",
	},
	{
		.key = "ro.telephony.default_network",
		.value = "27",
	},
	{
		.key = "ro.treble.enabled",
		.value = "true",
	},
	{
		.key = "ro.vendor.build.date",
		.value = "Tue Dec 12 05:18:54 UTC 2023",
	},
	{
		.key = "ro.vendor.build.date.utc",
		.value = "1702358334",
	},
	{
		.key = "ro.vendor.build.fingerprint",
		.value = "google/shiba/shiba:14/UQ1A.240105.004/11206848:userdebug/dev-keys",
	},
	{
		.key = "ro.zygote",
		.value = "zygote64",
	},
	{
		.key = "security.perf_harden",
		.value = "1",
	},
	{
		.key = "selinux.restorecon_recursive",
		.value = "/data/misc_ce/0",
	},
	{
		.key = "service.bootanim.exit",
		.value = "1",
	},
	{
		.key = "service.sf.present_timestamp",
		.value = "1",
	},
	{
		.key = "setupwizard.theme",
		.value = "glif_v4_light",
	},
	{
		.key = "sys.boot_completed",
		.value = "1",
	},
	{
		.key = "sys.oem_unlock_allowed",
		.value = "1",
	},
	{
		.key = "sys.rescue_boot_count",
		.value = "1",
	},
	{
		.key = "sys.retaildemo.enabled",
		.value = "0",
	},
	{
		.key = "sys.sysctl.extra_free_kbytes",
		.value = "30375",
	},
	{
		.key = "sys.usb.config",
		.value = "adb",
	},
	{
		.key = "sys.usb.configfs",
		.value = "2",
	},
	{
		.key = "sys.usb.controller",
		.value = "11210000.dwc3",
	},
	{
		.key = "sys.usb.ffs.ready",
		.value = "1",
	},
	{
		.key = "sys.usb.mtp.device_type",
		.value = "3",
	},
	{
		.key = "sys.user.0.ce_available",
		.value = "true",
	},
	{
		.key = "sys.wifitracing.started",
		.value = "1",
	},
	{
		.key = "vold.has_adoptable",
		.value = "0",
	},
	{
		.key = "vold.has_quota",
		.value = "1",
	},
	{NULL},
};
#endif /* __ANDROID__ */
