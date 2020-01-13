# -*- encoding: utf-8
# yapf: disable


checkname = 'esx_vsphere_hostsystem'


info = [[[u'config.multipathState.path',
          u'fc.2000001b329d47ad:2100001b329d47ad-fc.207000c0ffd76501:207000c0ffd76501-naa.600c0ff000d775af138c0a5601000000',
          u'active',
          u'fc.2000001b329d47ad:2100001b329d47ad-fc.207800c0ffd76501:217800c0ffd76501-naa.600c0ff000d7ba43d28c0a5601000000',
          u'active',
          u'fc.2001001b32bd47ad:2101001b32bd47ad-fc.207000c0ffd765cf:207000c0ffd765cf-naa.600c0ff000d77506480f3a5601000000',
          u'active',
          u'fc.2001001b32bd47ad:2101001b32bd47ad-fc.207000c0ffd765cf:207000c0ffd765cf-naa.600c0ff000d775066e113a5601000000',
          u'active',
          u'fc.2001001b32bd47ad:2101001b32bd47ad-fc.207800c0ffd765cf:217800c0ffd765cf-naa.600c0ff000d7bbd91d113a5601000000',
          u'active',
          u'sas.5005076b02c2618c-sas.626b411b9f799080-naa.50024e9201d0794b',
          u'active'],
         [u'hardware.biosInfo.biosVersion', u'-[MJE140BUS-1.18]-'],
         [u'hardware.biosInfo.releaseDate', u'2011-04-04T00:00:00Z'],
         [u'hardware.cpuInfo.hz', u'2500088448'],
         [u'hardware.cpuInfo.numCpuCores', u'8'],
         [u'hardware.cpuInfo.numCpuPackages', u'2'],
         [u'hardware.cpuInfo.numCpuThreads', u'8'],
         [u'hardware.cpuPkg.busHz.0', u'333345084'],
         [u'hardware.cpuPkg.busHz.1', u'333345066'],
         [u'hardware.cpuPkg.description.0',
          u'Intel(R)',
          u'Xeon(R)',
          u'CPU',
          u'E5420',
          u'@',
          u'2.50GHz'],
         [u'hardware.cpuPkg.description.1',
          u'Intel(R)',
          u'Xeon(R)',
          u'CPU',
          u'E5420',
          u'@',
          u'2.50GHz'],
         [u'hardware.cpuPkg.hz.0', u'2500088331'],
         [u'hardware.cpuPkg.hz.1', u'2500088566'],
         [u'hardware.cpuPkg.index.0', u'0'],
         [u'hardware.cpuPkg.index.1', u'1'],
         [u'hardware.cpuPkg.vendor.0', u'intel'],
         [u'hardware.cpuPkg.vendor.1', u'intel'],
         [u'hardware.memorySize', u'67913404416'],
         [u'hardware.systemInfo.model',
          u'IBM',
          u'eServer',
          u'BladeCenter',
          u'HS21',
          u'-[7995G3G]-'],
         [u'hardware.systemInfo.otherIdentifyingInfo.AssetTag.0', u'unknown'],
         [u'hardware.systemInfo.otherIdentifyingInfo.OemSpecificString.0',
          u'IBM',
          u'BaseBoard',
          u'Management',
          u'Controller',
          u'-[MJBT34A',
          u']-'],
         [u'hardware.systemInfo.otherIdentifyingInfo.OemSpecificString.1',
          u'IBM',
          u'Diagnostics',
          u'-[MJYT20AUS]-'],
         [u'hardware.systemInfo.otherIdentifyingInfo.ServiceTag.0',
          u'-[UUID:2DDCB758CE5611DD94EF00145EE1FCDA]-'],
         [u'hardware.systemInfo.otherIdentifyingInfo.ServiceTag.1', u'99C8785'],
         [u'hardware.systemInfo.uuid', u'c05d99a8-1861-b601-6927-001a645a8f28'],
         [u'hardware.systemInfo.vendor', u'IBM'],
         [u'name', u'srvesxblade06.comline.de'],
         [u'overallStatus', u'green'],
         [u'runtime.inMaintenanceMode', u'false'],
         [u'runtime.powerState', u'poweredOn'],
         [u'summary.quickStats.overallCpuUsage', u'10733'],
         [u'summary.quickStats.overallMemoryUsage', u'53325']],
        None]


discovery = {'': [],
             'cpu_usage': [(None, {})],
             'cpu_util_cluster': [],
             'maintenance': [(None, {'target_state': 'false'})],
             'mem_usage': [(None, 'esx_host_mem_default_levels')],
             'mem_usage_cluster': [],
             'multipath': [],
             'state': [(None, None)]}


checks = {'cpu_usage': [(None,
                         {},
                         [(0,
                           'Total CPU: 53.66%',
                           [('util', 53.66310144240145, None, None, 0, 100)]),
                           (0, '10.73GHz/20.00GHz', []),
                           (0, '2 sockets, 4 cores/socket, 8 threads', [])])],
          'maintenance': [(None,
                           {'target_state': 'false'},
                           [(0, 'System not in Maintenance mode', [])])],
          'mem_usage': [(None,
                         (80.0, 90.0),
                         [(1,
                           'Usage: 82.33% - 52.08 GB of 63.25 GB (warn/crit at 80.0%/90.0% used)',
                           [('usage',
                             55915315200.0,
                             54330723532.8,
                             61122063974.4,
                             0,
                             67913404416.0),
                            ('mem_total', 67913404416.0, None, None, None, None)])])],
          'state': [(None,
                     {},
                     [(0, 'Entity state: green', []),
                      (0, 'Power state: poweredOn', [])])]}
