open_wave_database {example/buggy_prj/buggy_prj.sim/sim_1/behav/xsim/tb_buggy_behav.wdb}
set sigs [get_wave_objects -filter {NAME =~ *}]
puts "===SIGNALS==="
foreach sig $sigs {
    puts [get_property NAME $sig]
}
puts "===END==="
exit
