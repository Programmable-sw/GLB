# Final packet load-balancing comparison

Scale: 128 nodes / 8 paths. Seeds: 13, 29, 47.

512-node resource gate: forced.

Primary metrics: healthy/asymmetric P2P uses p99 FCT; All-to-All uses CCT; mixed deployment uses bg=0 maximum FCT. Every raw row also retains mean/p50/p95/p99/p99.9/max, and mixed bg=0/bg=1 statistics separately.

## Three-seed results

| family | scenario | scheme | metric | geomean us | min | max | ECMP speedup |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p16 | mrc | all_to_all_cct_us | 29333.291 | 28070.100 | 31364.800 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p16 | n-mrc | all_to_all_cct_us | 29659.011 | 29105.900 | 30480.700 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p16 | ops | all_to_all_cct_us | 38081.920 | 36319.800 | 41785.100 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p16 | reps | all_to_all_cct_us | 26534.081 | 25754.300 | 28000.800 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p16 | sglb | all_to_all_cct_us | 26491.410 | 25917.300 | 27243.200 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p4 | mrc | all_to_all_cct_us | 41637.893 | 38979.900 | 44312.700 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p4 | n-mrc | all_to_all_cct_us | 37808.713 | 36049.000 | 39118.600 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p4 | ops | all_to_all_cct_us | 47168.619 | 44497.300 | 51393.000 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p4 | reps | all_to_all_cct_us | 35679.024 | 34836.100 | 37193.800 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p4 | sglb | all_to_all_cct_us | 39996.492 | 36585.400 | 43751.100 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p8 | mrc | all_to_all_cct_us | 33797.204 | 32784.400 | 35848.100 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p8 | n-mrc | all_to_all_cct_us | 31191.367 | 30844.300 | 31816.700 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p8 | ops | all_to_all_cct_us | 43107.757 | 41708.900 | 45739.200 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p8 | reps | all_to_all_cct_us | 30415.201 | 29803.800 | 30927.800 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_1024mib_p8 | sglb | all_to_all_cct_us | 30284.764 | 28445.200 | 31460.300 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p16 | mrc | all_to_all_cct_us | 7845.713 | 7389.780 | 8424.160 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p16 | n-mrc | all_to_all_cct_us | 7692.000 | 7519.380 | 7955.030 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p16 | ops | all_to_all_cct_us | 9126.715 | 8551.720 | 10272.700 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p16 | reps | all_to_all_cct_us | 7888.047 | 7813.380 | 8026.390 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p16 | sglb | all_to_all_cct_us | 7631.897 | 7554.300 | 7673.610 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p4 | mrc | all_to_all_cct_us | 11006.008 | 10061.400 | 12376.900 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p4 | n-mrc | all_to_all_cct_us | 11255.758 | 10507.100 | 12711.600 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p4 | ops | all_to_all_cct_us | 11823.695 | 10900.500 | 12931.600 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p4 | reps | all_to_all_cct_us | 10544.762 | 10189.500 | 10844.700 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p4 | sglb | all_to_all_cct_us | 10876.251 | 9927.120 | 11927.700 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p8 | mrc | all_to_all_cct_us | 9280.526 | 9126.980 | 9575.790 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p8 | n-mrc | all_to_all_cct_us | 9801.785 | 8883.680 | 10422.600 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p8 | ops | all_to_all_cct_us | 10222.732 | 9653.960 | 11334.000 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p8 | reps | all_to_all_cct_us | 9003.669 | 8836.010 | 9180.800 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_256mib_p8 | sglb | all_to_all_cct_us | 9022.463 | 8606.470 | 9241.440 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p16 | mrc | all_to_all_cct_us | 2004.494 | 1959.660 | 2042.320 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p16 | n-mrc | all_to_all_cct_us | 1989.239 | 1944.670 | 2036.190 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p16 | ops | all_to_all_cct_us | 2157.199 | 2052.290 | 2350.690 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p16 | reps | all_to_all_cct_us | 1993.414 | 1939.730 | 2075.640 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p16 | sglb | all_to_all_cct_us | 1953.119 | 1919.160 | 1982.350 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p4 | mrc | all_to_all_cct_us | 3037.561 | 2847.130 | 3221.070 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p4 | n-mrc | all_to_all_cct_us | 2564.801 | 2446.260 | 2652.650 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p4 | ops | all_to_all_cct_us | 2611.470 | 2472.190 | 2839.380 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p4 | reps | all_to_all_cct_us | 2377.657 | 2177.630 | 2713.570 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p4 | sglb | all_to_all_cct_us | 2455.454 | 2188.930 | 2921.230 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p8 | mrc | all_to_all_cct_us | 2027.678 | 1936.660 | 2135.090 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p8 | n-mrc | all_to_all_cct_us | 2119.304 | 2028.320 | 2204.430 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p8 | ops | all_to_all_cct_us | 2339.559 | 2220.160 | 2570.640 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p8 | reps | all_to_all_cct_us | 2095.147 | 2049.970 | 2184.970 |  |
| asymmetric_alltoall_background | asymmetric_alltoall_background_64mib_p8 | sglb | all_to_all_cct_us | 2000.008 | 1947.110 | 2035.170 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_16mib | mrc | p99_fct_us | 517.637 | 512.470 | 523.690 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_16mib | n-mrc | p99_fct_us | 401.854 | 383.483 | 441.083 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_16mib | ops | p99_fct_us | 907.194 | 777.462 | 1042.736 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_16mib | reps | p99_fct_us | 469.119 | 455.076 | 479.308 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_16mib | sglb | p99_fct_us | 410.646 | 407.950 | 412.842 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_4mib | mrc | p99_fct_us | 125.892 | 124.511 | 126.763 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_4mib | n-mrc | p99_fct_us | 103.863 | 103.353 | 104.180 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_4mib | ops | p99_fct_us | 234.045 | 202.448 | 260.832 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_4mib | reps | p99_fct_us | 127.545 | 126.198 | 128.477 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_4mib | sglb | p99_fct_us | 109.710 | 109.419 | 110.058 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_8mib | mrc | p99_fct_us | 250.653 | 249.626 | 251.933 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_8mib | n-mrc | p99_fct_us | 195.689 | 195.581 | 195.744 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_8mib | ops | p99_fct_us | 458.642 | 389.783 | 507.991 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_8mib | reps | p99_fct_us | 243.005 | 237.501 | 247.975 |  |
| asymmetric_p2p | asymmetric_p2p_permutation_8mib | sglb | p99_fct_us | 207.528 | 206.764 | 208.085 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_16mib | mrc | p99_fct_us | 519.600 | 513.777 | 524.822 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_16mib | n-mrc | p99_fct_us | 382.855 | 382.516 | 383.377 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_16mib | ops | p99_fct_us | 936.028 | 805.704 | 1062.909 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_16mib | reps | p99_fct_us | 452.666 | 445.989 | 460.049 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_16mib | sglb | p99_fct_us | 391.107 | 389.598 | 392.348 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_4mib | mrc | p99_fct_us | 125.388 | 125.204 | 125.646 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_4mib | n-mrc | p99_fct_us | 103.217 | 103.040 | 103.348 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_4mib | ops | p99_fct_us | 243.507 | 228.823 | 261.896 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_4mib | reps | p99_fct_us | 123.678 | 122.441 | 125.785 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_4mib | sglb | p99_fct_us | 107.601 | 106.999 | 108.567 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_8mib | mrc | p99_fct_us | 248.684 | 248.143 | 249.378 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_8mib | n-mrc | p99_fct_us | 195.181 | 194.871 | 195.345 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_8mib | ops | p99_fct_us | 482.648 | 439.071 | 520.274 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_8mib | reps | p99_fct_us | 234.746 | 232.071 | 238.275 |  |
| asymmetric_p2p | asymmetric_p2p_tornado_8mib | sglb | p99_fct_us | 201.962 | 201.527 | 202.541 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_100pct | mrc | p99_fct_us | 6698.235 | 6426.385 | 7229.652 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_100pct | n-mrc | p99_fct_us | 8066.717 | 7673.913 | 8782.249 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_100pct | ops | p99_fct_us | 7738.604 | 7339.488 | 8348.432 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_100pct | reps | p99_fct_us | 6606.897 | 6307.147 | 7156.405 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_100pct | sglb | p99_fct_us | 6727.762 | 6416.383 | 7385.255 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_40pct | mrc | p99_fct_us | 1227.935 | 1159.701 | 1307.177 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_40pct | n-mrc | p99_fct_us | 1220.985 | 1147.203 | 1311.104 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_40pct | ops | p99_fct_us | 1312.091 | 1254.560 | 1399.658 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_40pct | reps | p99_fct_us | 1222.450 | 1149.506 | 1309.698 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_40pct | sglb | p99_fct_us | 1220.216 | 1149.950 | 1304.030 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_60pct | mrc | p99_fct_us | 2057.893 | 1939.896 | 2305.131 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_60pct | n-mrc | p99_fct_us | 2013.450 | 1878.464 | 2251.561 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_60pct | ops | p99_fct_us | 2442.087 | 2353.938 | 2602.437 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_60pct | reps | p99_fct_us | 2025.012 | 1904.616 | 2263.897 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_60pct | sglb | p99_fct_us | 2008.914 | 1894.195 | 2250.409 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_80pct | mrc | p99_fct_us | 3899.754 | 3685.357 | 4359.612 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_80pct | n-mrc | p99_fct_us | 4061.313 | 3750.000 | 4696.816 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_80pct | ops | p99_fct_us | 5024.549 | 4659.651 | 5310.265 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_80pct | reps | p99_fct_us | 3826.848 | 3661.544 | 4154.901 |  |
| asymmetric_p2p | asymmetric_p2p_websearch_80pct | sglb | p99_fct_us | 3833.825 | 3605.403 | 4228.962 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p16 | mrc | all_to_all_cct_us | 24189.997 | 23878.900 | 24668.000 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p16 | n-mrc | all_to_all_cct_us | 28074.094 | 28050.600 | 28093.300 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p16 | ops | all_to_all_cct_us | 24120.025 | 23984.200 | 24222.100 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p16 | reps | all_to_all_cct_us | 23445.323 | 23381.600 | 23483.600 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p16 | sglb | all_to_all_cct_us | 25007.963 | 24941.000 | 25085.300 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p4 | mrc | all_to_all_cct_us | 33583.371 | 32250.400 | 34871.000 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p4 | n-mrc | all_to_all_cct_us | 37483.510 | 36081.800 | 38771.600 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p4 | ops | all_to_all_cct_us | 37922.031 | 36490.200 | 40010.200 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p4 | reps | all_to_all_cct_us | 37872.589 | 34744.200 | 40756.200 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p4 | sglb | all_to_all_cct_us | 40964.413 | 37987.100 | 46142.000 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p8 | mrc | all_to_all_cct_us | 28099.138 | 27181.900 | 29180.500 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p8 | n-mrc | all_to_all_cct_us | 30160.371 | 30016.300 | 30421.300 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p8 | ops | all_to_all_cct_us | 29016.613 | 28524.100 | 29601.500 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p8 | reps | all_to_all_cct_us | 28940.265 | 28176.200 | 29952.500 |  |
| healthy_alltoall | healthy_alltoall_1024mib_p8 | sglb | all_to_all_cct_us | 29909.034 | 29570.600 | 30262.000 |  |
| healthy_alltoall | healthy_alltoall_256mib_p16 | mrc | all_to_all_cct_us | 6842.448 | 6605.530 | 7143.150 |  |
| healthy_alltoall | healthy_alltoall_256mib_p16 | n-mrc | all_to_all_cct_us | 6980.919 | 6900.180 | 7126.280 |  |
| healthy_alltoall | healthy_alltoall_256mib_p16 | ops | all_to_all_cct_us | 7695.131 | 7215.770 | 8282.260 |  |
| healthy_alltoall | healthy_alltoall_256mib_p16 | reps | all_to_all_cct_us | 7721.784 | 7168.200 | 8107.750 |  |
| healthy_alltoall | healthy_alltoall_256mib_p16 | sglb | all_to_all_cct_us | 7486.722 | 7095.250 | 7715.310 |  |
| healthy_alltoall | healthy_alltoall_256mib_p4 | mrc | all_to_all_cct_us | 9081.085 | 8886.100 | 9466.310 |  |
| healthy_alltoall | healthy_alltoall_256mib_p4 | n-mrc | all_to_all_cct_us | 10562.938 | 10144.000 | 11343.400 |  |
| healthy_alltoall | healthy_alltoall_256mib_p4 | ops | all_to_all_cct_us | 11984.931 | 11489.200 | 12309.100 |  |
| healthy_alltoall | healthy_alltoall_256mib_p4 | reps | all_to_all_cct_us | 10734.966 | 10141.700 | 11198.600 |  |
| healthy_alltoall | healthy_alltoall_256mib_p4 | sglb | all_to_all_cct_us | 10236.264 | 9793.520 | 11023.900 |  |
| healthy_alltoall | healthy_alltoall_256mib_p8 | mrc | all_to_all_cct_us | 8424.365 | 8016.750 | 8862.010 |  |
| healthy_alltoall | healthy_alltoall_256mib_p8 | n-mrc | all_to_all_cct_us | 8638.685 | 8402.750 | 8862.670 |  |
| healthy_alltoall | healthy_alltoall_256mib_p8 | ops | all_to_all_cct_us | 8855.768 | 8222.910 | 9377.840 |  |
| healthy_alltoall | healthy_alltoall_256mib_p8 | reps | all_to_all_cct_us | 8686.686 | 8148.500 | 9218.210 |  |
| healthy_alltoall | healthy_alltoall_256mib_p8 | sglb | all_to_all_cct_us | 8451.942 | 8415.330 | 8501.620 |  |
| healthy_alltoall | healthy_alltoall_64mib_p16 | mrc | all_to_all_cct_us | 1734.953 | 1728.640 | 1746.850 |  |
| healthy_alltoall | healthy_alltoall_64mib_p16 | n-mrc | all_to_all_cct_us | 1798.733 | 1768.330 | 1814.850 |  |
| healthy_alltoall | healthy_alltoall_64mib_p16 | ops | all_to_all_cct_us | 1812.933 | 1788.080 | 1849.910 |  |
| healthy_alltoall | healthy_alltoall_64mib_p16 | reps | all_to_all_cct_us | 1807.221 | 1792.240 | 1823.700 |  |
| healthy_alltoall | healthy_alltoall_64mib_p16 | sglb | all_to_all_cct_us | 1922.617 | 1899.060 | 1951.010 |  |
| healthy_alltoall | healthy_alltoall_64mib_p4 | mrc | all_to_all_cct_us | 2254.895 | 2143.610 | 2388.880 |  |
| healthy_alltoall | healthy_alltoall_64mib_p4 | n-mrc | all_to_all_cct_us | 2306.478 | 2158.720 | 2400.680 |  |
| healthy_alltoall | healthy_alltoall_64mib_p4 | ops | all_to_all_cct_us | 2996.682 | 2781.510 | 3148.380 |  |
| healthy_alltoall | healthy_alltoall_64mib_p4 | reps | all_to_all_cct_us | 2455.598 | 2185.350 | 2687.810 |  |
| healthy_alltoall | healthy_alltoall_64mib_p4 | sglb | all_to_all_cct_us | 2332.819 | 2122.350 | 2563.520 |  |
| healthy_alltoall | healthy_alltoall_64mib_p8 | mrc | all_to_all_cct_us | 1710.525 | 1696.700 | 1726.810 |  |
| healthy_alltoall | healthy_alltoall_64mib_p8 | n-mrc | all_to_all_cct_us | 1842.966 | 1812.980 | 1858.360 |  |
| healthy_alltoall | healthy_alltoall_64mib_p8 | ops | all_to_all_cct_us | 1913.575 | 1868.270 | 1943.550 |  |
| healthy_alltoall | healthy_alltoall_64mib_p8 | reps | all_to_all_cct_us | 1777.680 | 1768.750 | 1788.920 |  |
| healthy_alltoall | healthy_alltoall_64mib_p8 | sglb | all_to_all_cct_us | 1854.509 | 1828.360 | 1892.780 |  |
| healthy_p2p | healthy_p2p_permutation_16mib | ecmp | p99_fct_us | 1440.333 | 1331.470 | 1653.935 | 1.0000x |
| healthy_p2p | healthy_p2p_permutation_16mib | mrc | p99_fct_us | 356.237 | 356.180 | 356.272 | 4.0432x |
| healthy_p2p | healthy_p2p_permutation_16mib | n-mrc | p99_fct_us | 356.254 | 356.202 | 356.333 | 4.0430x |
| healthy_p2p | healthy_p2p_permutation_16mib | ops | p99_fct_us | 742.452 | 720.400 | 754.531 | 1.9400x |
| healthy_p2p | healthy_p2p_permutation_16mib | reps | p99_fct_us | 459.421 | 455.511 | 463.519 | 3.1351x |
| healthy_p2p | healthy_p2p_permutation_16mib | sglb | p99_fct_us | 392.159 | 390.592 | 393.789 | 3.6728x |
| healthy_p2p | healthy_p2p_permutation_4mib | ecmp | p99_fct_us | 380.890 | 349.408 | 438.847 | 1.0000x |
| healthy_p2p | healthy_p2p_permutation_4mib | mrc | p99_fct_us | 96.715 | 96.657 | 96.750 | 3.9383x |
| healthy_p2p | healthy_p2p_permutation_4mib | n-mrc | p99_fct_us | 96.731 | 96.680 | 96.810 | 3.9376x |
| healthy_p2p | healthy_p2p_permutation_4mib | ops | p99_fct_us | 201.254 | 199.203 | 204.277 | 1.8926x |
| healthy_p2p | healthy_p2p_permutation_4mib | reps | p99_fct_us | 125.675 | 123.724 | 127.925 | 3.0307x |
| healthy_p2p | healthy_p2p_permutation_4mib | sglb | p99_fct_us | 103.946 | 103.860 | 104.093 | 3.6643x |
| healthy_p2p | healthy_p2p_permutation_8mib | ecmp | p99_fct_us | 723.938 | 671.788 | 835.057 | 1.0000x |
| healthy_p2p | healthy_p2p_permutation_8mib | mrc | p99_fct_us | 183.222 | 183.165 | 183.257 | 3.9511x |
| healthy_p2p | healthy_p2p_permutation_8mib | n-mrc | p99_fct_us | 183.239 | 183.187 | 183.318 | 3.9508x |
| healthy_p2p | healthy_p2p_permutation_8mib | ops | p99_fct_us | 389.182 | 381.495 | 397.687 | 1.8602x |
| healthy_p2p | healthy_p2p_permutation_8mib | reps | p99_fct_us | 238.915 | 237.663 | 240.975 | 3.0301x |
| healthy_p2p | healthy_p2p_permutation_8mib | sglb | p99_fct_us | 198.717 | 197.883 | 199.691 | 3.6431x |
| healthy_p2p | healthy_p2p_tornado_16mib | ecmp | p99_fct_us | 1357.291 | 1349.447 | 1370.420 | 1.0000x |
| healthy_p2p | healthy_p2p_tornado_16mib | mrc | p99_fct_us | 355.667 | 355.592 | 355.705 | 3.8162x |
| healthy_p2p | healthy_p2p_tornado_16mib | n-mrc | p99_fct_us | 355.671 | 355.590 | 355.735 | 3.8161x |
| healthy_p2p | healthy_p2p_tornado_16mib | ops | p99_fct_us | 721.382 | 682.787 | 752.065 | 1.8815x |
| healthy_p2p | healthy_p2p_tornado_16mib | reps | p99_fct_us | 427.761 | 417.702 | 437.451 | 3.1730x |
| healthy_p2p | healthy_p2p_tornado_16mib | sglb | p99_fct_us | 366.882 | 366.363 | 367.495 | 3.6995x |
| healthy_p2p | healthy_p2p_tornado_4mib | ecmp | p99_fct_us | 345.446 | 337.102 | 352.564 | 1.0000x |
| healthy_p2p | healthy_p2p_tornado_4mib | mrc | p99_fct_us | 96.144 | 96.069 | 96.182 | 3.5930x |
| healthy_p2p | healthy_p2p_tornado_4mib | n-mrc | p99_fct_us | 96.148 | 96.067 | 96.212 | 3.5929x |
| healthy_p2p | healthy_p2p_tornado_4mib | ops | p99_fct_us | 198.226 | 183.097 | 212.494 | 1.7427x |
| healthy_p2p | healthy_p2p_tornado_4mib | reps | p99_fct_us | 120.371 | 118.969 | 122.609 | 2.8699x |
| healthy_p2p | healthy_p2p_tornado_4mib | sglb | p99_fct_us | 101.486 | 101.392 | 101.577 | 3.4039x |
| healthy_p2p | healthy_p2p_tornado_8mib | ecmp | p99_fct_us | 681.114 | 678.699 | 685.712 | 1.0000x |
| healthy_p2p | healthy_p2p_tornado_8mib | mrc | p99_fct_us | 182.652 | 182.577 | 182.689 | 3.7290x |
| healthy_p2p | healthy_p2p_tornado_8mib | n-mrc | p99_fct_us | 182.656 | 182.575 | 182.720 | 3.7289x |
| healthy_p2p | healthy_p2p_tornado_8mib | ops | p99_fct_us | 379.386 | 351.572 | 422.215 | 1.7953x |
| healthy_p2p | healthy_p2p_tornado_8mib | reps | p99_fct_us | 225.454 | 221.279 | 229.154 | 3.0211x |
| healthy_p2p | healthy_p2p_tornado_8mib | sglb | p99_fct_us | 190.775 | 190.516 | 191.216 | 3.5703x |
| healthy_p2p | healthy_p2p_websearch_100pct | ecmp | p99_fct_us | 7468.735 | 7107.192 | 8023.865 | 1.0000x |
| healthy_p2p | healthy_p2p_websearch_100pct | mrc | p99_fct_us | 6642.016 | 6349.338 | 7196.341 | 1.1245x |
| healthy_p2p | healthy_p2p_websearch_100pct | n-mrc | p99_fct_us | 8029.471 | 7579.863 | 8800.198 | 0.9302x |
| healthy_p2p | healthy_p2p_websearch_100pct | ops | p99_fct_us | 6887.548 | 6620.198 | 7412.702 | 1.0844x |
| healthy_p2p | healthy_p2p_websearch_100pct | reps | p99_fct_us | 6629.070 | 6373.584 | 7167.896 | 1.1267x |
| healthy_p2p | healthy_p2p_websearch_100pct | sglb | p99_fct_us | 6647.017 | 6352.454 | 7224.609 | 1.1236x |
| healthy_p2p | healthy_p2p_websearch_40pct | ecmp | p99_fct_us | 1750.587 | 1693.963 | 1839.052 | 1.0000x |
| healthy_p2p | healthy_p2p_websearch_40pct | mrc | p99_fct_us | 1221.461 | 1149.082 | 1320.056 | 1.4332x |
| healthy_p2p | healthy_p2p_websearch_40pct | n-mrc | p99_fct_us | 1215.483 | 1134.698 | 1310.997 | 1.4402x |
| healthy_p2p | healthy_p2p_websearch_40pct | ops | p99_fct_us | 1257.471 | 1185.571 | 1337.105 | 1.3921x |
| healthy_p2p | healthy_p2p_websearch_40pct | reps | p99_fct_us | 1222.338 | 1151.499 | 1315.575 | 1.4322x |
| healthy_p2p | healthy_p2p_websearch_40pct | sglb | p99_fct_us | 1220.336 | 1145.550 | 1313.534 | 1.4345x |
| healthy_p2p | healthy_p2p_websearch_60pct | ecmp | p99_fct_us | 3012.509 | 2808.893 | 3339.952 | 1.0000x |
| healthy_p2p | healthy_p2p_websearch_60pct | mrc | p99_fct_us | 1993.916 | 1876.562 | 2228.144 | 1.5109x |
| healthy_p2p | healthy_p2p_websearch_60pct | n-mrc | p99_fct_us | 2016.278 | 1905.282 | 2242.568 | 1.4941x |
| healthy_p2p | healthy_p2p_websearch_60pct | ops | p99_fct_us | 2153.383 | 2003.009 | 2392.372 | 1.3990x |
| healthy_p2p | healthy_p2p_websearch_60pct | reps | p99_fct_us | 2047.816 | 1938.805 | 2271.719 | 1.4711x |
| healthy_p2p | healthy_p2p_websearch_60pct | sglb | p99_fct_us | 2014.150 | 1898.243 | 2242.619 | 1.4957x |
| healthy_p2p | healthy_p2p_websearch_80pct | ecmp | p99_fct_us | 5033.447 | 4771.721 | 5396.578 | 1.0000x |
| healthy_p2p | healthy_p2p_websearch_80pct | mrc | p99_fct_us | 3827.689 | 3595.155 | 4274.937 | 1.3150x |
| healthy_p2p | healthy_p2p_websearch_80pct | n-mrc | p99_fct_us | 3994.878 | 3737.039 | 4533.002 | 1.2600x |
| healthy_p2p | healthy_p2p_websearch_80pct | ops | p99_fct_us | 4138.392 | 3859.659 | 4572.166 | 1.2163x |
| healthy_p2p | healthy_p2p_websearch_80pct | reps | p99_fct_us | 3804.222 | 3610.941 | 4143.804 | 1.3231x |
| healthy_p2p | healthy_p2p_websearch_80pct | sglb | p99_fct_us | 3840.165 | 3611.673 | 4265.565 | 1.3107x |
| mixed_deployment | mixed_deployment_permutation_16mib | mrc | main_max_fct_us | 418.779 | 403.044 | 442.087 |  |
| mixed_deployment | mixed_deployment_permutation_16mib | n-mrc | main_max_fct_us | 448.114 | 435.800 | 459.253 |  |
| mixed_deployment | mixed_deployment_permutation_16mib | ops | main_max_fct_us | 823.353 | 727.614 | 934.690 |  |
| mixed_deployment | mixed_deployment_permutation_16mib | reps | main_max_fct_us | 490.984 | 459.346 | 517.408 |  |
| mixed_deployment | mixed_deployment_permutation_16mib | sglb | main_max_fct_us | 403.301 | 397.261 | 406.960 |  |
| mixed_deployment | mixed_deployment_permutation_4mib | mrc | main_max_fct_us | 116.244 | 113.500 | 119.399 |  |
| mixed_deployment | mixed_deployment_permutation_4mib | n-mrc | main_max_fct_us | 106.982 | 104.994 | 108.249 |  |
| mixed_deployment | mixed_deployment_permutation_4mib | ops | main_max_fct_us | 225.129 | 196.967 | 248.742 |  |
| mixed_deployment | mixed_deployment_permutation_4mib | reps | main_max_fct_us | 142.545 | 132.452 | 150.576 |  |
| mixed_deployment | mixed_deployment_permutation_4mib | sglb | main_max_fct_us | 112.053 | 110.389 | 113.043 |  |
| mixed_deployment | mixed_deployment_permutation_8mib | mrc | main_max_fct_us | 221.640 | 213.233 | 231.020 |  |
| mixed_deployment | mixed_deployment_permutation_8mib | n-mrc | main_max_fct_us | 201.467 | 197.912 | 203.324 |  |
| mixed_deployment | mixed_deployment_permutation_8mib | ops | main_max_fct_us | 453.979 | 388.337 | 508.787 |  |
| mixed_deployment | mixed_deployment_permutation_8mib | reps | main_max_fct_us | 264.826 | 248.065 | 281.752 |  |
| mixed_deployment | mixed_deployment_permutation_8mib | sglb | main_max_fct_us | 211.545 | 209.318 | 213.337 |  |
| mixed_deployment | mixed_deployment_tornado_16mib | mrc | main_max_fct_us | 382.634 | 380.070 | 385.682 |  |
| mixed_deployment | mixed_deployment_tornado_16mib | n-mrc | main_max_fct_us | 359.796 | 359.460 | 360.295 |  |
| mixed_deployment | mixed_deployment_tornado_16mib | ops | main_max_fct_us | 769.883 | 744.550 | 813.629 |  |
| mixed_deployment | mixed_deployment_tornado_16mib | reps | main_max_fct_us | 480.669 | 468.106 | 487.408 |  |
| mixed_deployment | mixed_deployment_tornado_16mib | sglb | main_max_fct_us | 367.826 | 366.871 | 369.198 |  |
| mixed_deployment | mixed_deployment_tornado_4mib | mrc | main_max_fct_us | 106.893 | 106.757 | 107.095 |  |
| mixed_deployment | mixed_deployment_tornado_4mib | n-mrc | main_max_fct_us | 100.055 | 99.612 | 100.786 |  |
| mixed_deployment | mixed_deployment_tornado_4mib | ops | main_max_fct_us | 208.040 | 193.892 | 225.702 |  |
| mixed_deployment | mixed_deployment_tornado_4mib | reps | main_max_fct_us | 129.671 | 126.016 | 133.042 |  |
| mixed_deployment | mixed_deployment_tornado_4mib | sglb | main_max_fct_us | 103.016 | 102.670 | 103.504 |  |
| mixed_deployment | mixed_deployment_tornado_8mib | mrc | main_max_fct_us | 202.271 | 201.094 | 204.454 |  |
| mixed_deployment | mixed_deployment_tornado_8mib | n-mrc | main_max_fct_us | 186.711 | 186.060 | 187.184 |  |
| mixed_deployment | mixed_deployment_tornado_8mib | ops | main_max_fct_us | 391.321 | 372.697 | 416.386 |  |
| mixed_deployment | mixed_deployment_tornado_8mib | reps | main_max_fct_us | 249.731 | 240.440 | 256.178 |  |
| mixed_deployment | mixed_deployment_tornado_8mib | sglb | main_max_fct_us | 193.290 | 192.914 | 193.859 |  |

## Mixed deployment: separate maxima

| scenario | scheme | main bg=0 max geomean us | ECMP bg=1 max geomean us |
| --- | --- | ---: | ---: |
| mixed_deployment_permutation_16mib | mrc | 418.779 | 906.181 |
| mixed_deployment_permutation_16mib | n-mrc | 448.114 | 777.895 |
| mixed_deployment_permutation_16mib | ops | 823.353 | 943.609 |
| mixed_deployment_permutation_16mib | reps | 490.984 | 643.679 |
| mixed_deployment_permutation_16mib | sglb | 403.301 | 791.318 |
| mixed_deployment_permutation_4mib | mrc | 116.244 | 249.104 |
| mixed_deployment_permutation_4mib | n-mrc | 106.982 | 199.510 |
| mixed_deployment_permutation_4mib | ops | 225.129 | 272.815 |
| mixed_deployment_permutation_4mib | reps | 142.545 | 180.490 |
| mixed_deployment_permutation_4mib | sglb | 112.053 | 195.950 |
| mixed_deployment_permutation_8mib | mrc | 221.640 | 473.760 |
| mixed_deployment_permutation_8mib | n-mrc | 201.467 | 394.507 |
| mixed_deployment_permutation_8mib | ops | 453.979 | 516.358 |
| mixed_deployment_permutation_8mib | reps | 264.826 | 344.794 |
| mixed_deployment_permutation_8mib | sglb | 211.545 | 398.029 |
| mixed_deployment_tornado_16mib | mrc | 382.634 | 816.524 |
| mixed_deployment_tornado_16mib | n-mrc | 359.796 | 613.124 |
| mixed_deployment_tornado_16mib | ops | 769.883 | 934.623 |
| mixed_deployment_tornado_16mib | reps | 480.669 | 584.957 |
| mixed_deployment_tornado_16mib | sglb | 367.826 | 613.859 |
| mixed_deployment_tornado_4mib | mrc | 106.893 | 226.724 |
| mixed_deployment_tornado_4mib | n-mrc | 100.055 | 145.486 |
| mixed_deployment_tornado_4mib | ops | 208.040 | 291.337 |
| mixed_deployment_tornado_4mib | reps | 129.671 | 163.897 |
| mixed_deployment_tornado_4mib | sglb | 103.016 | 142.244 |
| mixed_deployment_tornado_8mib | mrc | 202.271 | 447.571 |
| mixed_deployment_tornado_8mib | n-mrc | 186.711 | 308.601 |
| mixed_deployment_tornado_8mib | ops | 391.321 | 523.433 |
| mixed_deployment_tornado_8mib | reps | 249.731 | 310.340 |
| mixed_deployment_tornado_8mib | sglb | 193.290 | 301.804 |

Validated raw cells: 690.

WebSearch uses the repository's digitized proxy CDF, not an exact machine-readable trace extracted from the REPS paper.
