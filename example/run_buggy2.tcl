open_project buggy_prj/buggy_prj.xpr
set_property xsim.simulate.log_all_signals true [get_filesets sim_1]
launch_simulation
puts \"===SIM_DONE===\"
