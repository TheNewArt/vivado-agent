# Create Vivado project for counter example
set script_dir [file dirname [info script]]
set prj_dir [file join $script_dir "counter_prj"]
set rtl_dir [file join $script_dir "rtl"]
set tb_dir  [file join $script_dir "tb"]

create_project -force counter_prj $prj_dir -part xc7a35tcpg236-1

add_files -norecurse [list \
  [file normalize [file join $rtl_dir "counter.v"]] \
  [file normalize [file join $rtl_dir "debouncer.v"]] \
]

add_files -fileset sim_1 -norecurse [file normalize [file join $tb_dir "tb_counter.sv"]]

set_property top counter [current_fileset]
set_property top tb_counter [get_filesets sim_1]
set_property -name {xsim.simulate.runtime} -value {1000ns} [get_filesets sim_1]

close_project
puts "Project created: $prj_dir"