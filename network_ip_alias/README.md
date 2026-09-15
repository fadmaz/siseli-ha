# Network IP Alias

This Home Assistant local add-on adds a secondary IPv4 address to an existing host
interface while it is running. It removes the address during a normal stop.

Set `INTERFACE` to the active host interface, commonly `end0` for Ethernet or
`wlan0` for Wi-Fi. Set `IP_ADDRESS` to an unused address with CIDR prefix, such as
`192.168.1.20/24`.

The add-on needs `host_network` and `NET_ADMIN`. It does not configure DNS, routing,
or firewall rules. Confirm the selected address is outside DHCP allocation or reserved
in the router before enabling it.