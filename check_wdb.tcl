open_wave_database {example/buggy_prj/buggy_prj.sim/sim_1/behav/xsim/tb_buggy_behav.wdb}
open_wave_config [current_wave_config]
set sigs [get_wave_objects -filter {NAME =~ *count*}]
puts "===SIGNALS==="
foreach sig [get_wave_objects -filter {NAME =~ *count*}] {
    puts [get_property NAME ]
}
puts "---"
foreach sig [get_wave_objects -filter {NAME =~ *multi_drive*}] {
    puts [get_property NAME ]
}
puts "===END==="
exit
