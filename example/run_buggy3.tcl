open_project buggy_prj/buggy_prj.xpr
set_property xsim.simulate.log_all_signals true [get_filesets sim_1]
set_property xsim.simulate.waveform_storage compact [get_filesets sim_1]
launch_simulation
# After simulation, export WDB with signals
current_wave_config [get_filesets sim_1]
write_waveform -force
puts \"===WAVEFORM_SAVED===\"
