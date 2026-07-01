set script_dir [file dirname [info script]]
set prj_path [file join $script_dir "counter_prj" "counter_prj.xpr"]

open_project $prj_path
set_param general.maxThreads 4
set_property xsim.simulate.log_all_signals false [get_filesets sim_1]
set_property xsim.simulate.waveform_storage compact [get_filesets sim_1]
launch_simulation -step compile
set comp_status [get_property STATUS [get_runs sim_1]]
while {$comp_status != "end"} {
  after 100
  set comp_status [get_property STATUS [get_runs sim_1]]
}
puts "Compile done: $comp_status"

launch_simulation -step elaborate
set elabor_status [get_property STATUS [get_runs sim_1]]
while {$elabor_status != "end"} {
  after 100
  set elabor_status [get_property STATUS [get_runs sim_1]]
}
puts "Elaborate done: $elabor_status"

launch_simulation -step simulate
set sim_status [get_property STATUS [get_runs sim_1]]
while {$sim_status != "end"} {
  after 100
  set sim_status [get_property STATUS [get_runs sim_1]]
}
puts "=== SIMULATION DONE ==="