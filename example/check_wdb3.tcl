open_wave_database {buggy_prj/buggy_prj.sim/sim_1/behav/xsim/tb_buggy_behav.wdb}
open_wave_config -database [current_wave_database]
set sigs [get_wave_objects -all]
puts "===SIGNALS==="
foreach sig  {
    puts [get_property NAME ]
}
puts "===END==="
