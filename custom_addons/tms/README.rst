=========
Transport
=========

The Transport Management Systems (TMS) module manages the workflow of
creating transport operations within Odoo. This module provides features
such as creating teams, stages, crews, routes, and vehicles, allowing
businesses to efficiently handle various aspects of their transport
operations.

.. IMPORTANT::
   This is an alpha version, the data model and design can change at any time without warning.
   Only for development or testing purpose, do not use in production.

**Table of contents**

.. contents::
   :local:

Configuration
=============

TMS module can be used with advanced configuration that enable extra
features complementing the base module.

Units of Measure
----------------

In the base module units of measure (UoM) aren't considered. They have
to be manually enabled in the settings.

1. Go to TMS > Configuration > Settings > Units
2. Activate the "Units of measure" checkbox and save the changes
3. Now UoM appear in the module in the distance, length, weight and time
   fields with a default unit that is computed.
4. To change the default UoM of an specific magnitude, select the
   default UoM in its corresponding measurement in the settings.

Manage teams and crews
----------------------

Team management is an advanced feature that can be configured in the TMS
module. They are a predefined group of employees that have predefined
drivers and vehicles. "Mexican Transportation Company", i.e., has the
"Mexico City Team" and "Querétaro Team" with their own vehicles and
drivers.

1. Go to TMS > Configuration > Settings > Drivers
2. Activate the "Manage Teams" checkbox and save the changes
3. Go to TMS > Configuration > Drivers > Teams
4. A default team is created. Edit this team or create a new one
5. Write the team's name, select the drivers and the vehicles
6. If it's configured, you can also select crews for this team
7. When a trip is created, you can assign the trip to an specific team.

Crew management is an advanced feature that can be configured in the TMS
module. They are a predefined group of drivers and employees in a
vehicle. An airplane, i.e., has its pilots and crew members that attend
the passengers.

1. Go to TMS > Configuration > Settings > Drivers
2. Activate the "Manage Crews" checkbox and save the changes
3. Go to TMS > Configuration > Drivers > Crews
4. Create a new crew and assign it a name
5. Select the drivers, the personnel, and a default vehicle that the
   crew uses
6. When a trip is created, you can assign the trip to an specific crew

Routes and stop locations
-------------------------

Routes can be used to establish predetermined trips from one location to
another.

1. Go to TMS > Configuration > Settings > Trips
2. Activate the "Routes" checkbox and save the changes
3. Go to Master Data > Routes and create a new route
4. Write the route's name, select the origin location and select the
   origin destination
5. Create a new trip and enable the "Use a predefined route" checkbox
6. Select the route you have created for the trip

When activating routes in the settings, the option for creating stop
locations in routes is enabled.

1. Go to TMS > Configuration > Settings > Trips
2. Activate the "Stops" checkbox and save the changes
3. Go to Master Data > Routes and create a new route or select and
   existing one
4. Enable the "Stop locations" checkbox
5. Write the locations in the "Stop locations" page

Usage
=====

Vehicles
--------

1. Go to TMS application.
2. Go to Master Data > Vehicles in the menu.
3. Create a new vehicle with the desired model (required).
4. In the TMS page select the operation type (Cargo/Passenger).
5. Type the capacity of the vehicle.

Drivers
-------

1. Go to TMS application.
2. Go to Master Data > Drivers in the menu.
3. Create a new driver.

Locations
---------

1. Go to TMS application.
2. Go to Master Data > Locations in the menu.
3. Create a new location.

Orders
------

1. Go to TMS application.
2. Create a new trip from the dashboard.
3. Select a driver, a vehicle, an origin location and a destination
   location.
4. In the planning page select the start date, the duration and the
   scheduled end.
5. Once confirmed, click 'START' button to start the trip.
6. Once the trip is completed, click 'END' button to end the trip.

Credits
=======

Authors
-------

* Open Source Integrators

Contributors
------------

- Santiago Rodriguez srodriguez@opensourceintegrators.com
- Edgar Martinez emartinez@opensourceintegrators.com
- Maxime Chambreuil mchambreuil@opensourceintegrators.com
- Israel Cruz israel.cruz@argil.mx
- Alan Ramos alan.ramos@jarsa.com.mx
- Luis Triana luis.triana@jarsa.com.mx
- Sarai Osorio sarai.osorio@jarsa.com.mx
- Oscar Garza oscar.garza@jarsa.com.mx
- Hector Camacho hector.camacho@jarsa.com.mx
- Luis Miguel Guzmán miguel.ruiz@jarsa.com.mx
- Isabel Ávila isabel.esparza@jarsa.com.mx
- Erick Reza alexis.reza.s@gmail.com
- Tecnativa: \* Sergio Teruel \* Carlos Dauden

Other credits
-------------

- Open Source Integrators https://opensourceintegrators.com

This copy is bundled with Premier Transport TMS by RLFB (www.riamona.com). RLFB removed the upstream project's badges, issue-tracker and maintainer sections; the code, the licence and the copyright notices are unchanged.
