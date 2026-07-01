open_wave_database {buggy_prj/buggy_prj.sim/sim_1/behav/xsim/tb_buggy_behav.wdb}
create_wave_config
add_wave -r /
write_wave -format vcd -force
puts "VCD exported"
exit
