#!/bin/bash
# Runs INSIDE ros2_gazebo_aruco:v1 (ROS 2 Humble), started by run.py with --network none and a 3-minute limit.
# /check = this directory (read-only), /repo/contract = the record contract (read-only), /out = evidence (rw).
# Never run it on the host.

source /opt/ros/humble/setup.bash
export ROS_LOCALHOST_ONLY=1
cd /out || exit 1

mkdir -p msg
cp /opt/ros/humble/share/diagnostic_msgs/msg/DiagnosticArray.msg \
   /opt/ros/humble/share/diagnostic_msgs/msg/DiagnosticStatus.msg \
   /opt/ros/humble/share/diagnostic_msgs/msg/KeyValue.msg \
   /opt/ros/humble/share/std_msgs/msg/Header.msg \
   /opt/ros/humble/share/builtin_interfaces/msg/Time.msg msg/
ros2 interface show diagnostic_msgs/msg/DiagnosticArray > ros2_interface_show.txt 2>&1
ls /opt/ros/humble/share | grep -i -E '^diagnostic' > ros2_diagnostic_packages.txt

# a stock rclpy node publishing the stripped records, as any ROS 2 driver would
python3 /check/ros2_publish.py /out/stock_array.json 90 > publisher.log 2>&1 &
publisher=$!

timeout 40 ros2 topic echo /diagnostics --once > ros2_topic_echo.txt 2> ros2_topic_echo.err
echo "exit $?" >> ros2_topic_echo.err
timeout 20 ros2 topic info -v /diagnostics > ros2_topic_info.txt 2>&1
timeout 60 python3 /check/ros2_verify.py /out > verify.log 2>&1
echo "exit $?" >> verify.log

kill "$publisher" 2> /dev/null
wait "$publisher" 2> /dev/null
ros2 daemon stop > /dev/null 2>&1
exit 0
