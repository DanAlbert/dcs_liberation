import math
import operator
import typing
import random

from game import db
from gen import Conflict
from gen.flights.ai_flight_planner_db import INTERCEPT_CAPABLE, CAP_CAPABLE, CAS_CAPABLE, SEAD_CAPABLE
from gen.flights.flight import Flight, FlightType, FlightWaypoint

# TODO : Ideally should be based on the aircraft type instead / Availability of fuel
STRIKE_MAX_RANGE = 1500000
SEAD_MAX_RANGE = 1500000

MAX_NUMBER_OF_INTERCEPTION_GROUP = 3
MISSION_DURATION = 120 # in minutes
CAP_EVERY_X_MINUTES = 20
CAS_EVERY_X_MINUTES = 30
SEAD_EVERY_X_MINUTES = 40
STRIKE_EVERY_X_MINUTES = 40

INGRESS_EGRESS_DISTANCE = 45000
INGRESS_ALT = 6096 # 20k feet
EGRESS_ALT = 6096 # 20k feet
PATROL_ALT_RANGE = (3600, 9200)
NAV_ALT = 9144

class FlightPlanner:

    def __init__(self, from_cp, game):
        # TODO : have the flight planner depend on a 'stance' setting : [Defensive, Aggresive... etc] and faction doctrine
        # TODO : the flight planner should plan package and operations
        self.from_cp = from_cp
        self.game = game
        self.aircraft_inventory = {} # local copy of the airbase inventory

    def reset(self):
        """
        Reset the planned flights and available units
        """
        self.aircraft_inventory = dict({k: v for k, v in self.from_cp.base.aircraft.items()})
        self.interceptor_flights = []
        self.cap_flights = []
        self.cas_flights = []
        self.strike_flights = []
        self.sead_flights = []
        self.custom_flights = []
        self.flights = []
        self.potential_sead_targets = []
        self.potential_strike_targets = []

    def plan_flights(self):

        self.reset()
        self.compute_sead_targets()
        self.compute_strike_targets()

        # The priority is to assign air-superiority fighter or interceptor to interception roles, so they can scramble if there is an attacker
        #self.commision_interceptors()

        # Then some CAP patrol for the next 2 hours
        self.commision_cap()

        # Then setup cas
        self.commision_cas()

        # Then prepare some sead flights if required
        self.commision_sead()

        self.commision_strike()

        # TODO : commision STRIKE / ANTISHIP

    def remove_flight(self, index):
        try:
            flight = self.flights[index]
            if flight in self.interceptor_flights: self.interceptor_flights.remove(flight)
            if flight in self.cap_flights: self.cap_flights.remove(flight)
            if flight in self.cas_flights: self.cas_flights.remove(flight)
            if flight in self.strike_flights: self.strike_flights.remove(flight)
            if flight in self.sead_flights: self.sead_flights.remove(flight)
            if flight in self.custom_flights: self.custom_flights.remove(flight)
            self.flights.remove(flight)
        except IndexError:
            return


    def commision_interceptors(self):
        """
        Pick some aircraft to assign them to interception roles
        """

        # At least try to generate one interceptor group
        number_of_interceptor_groups = min(max(sum([v for k, v in self.aircraft_inventory.items()]) / 4, MAX_NUMBER_OF_INTERCEPTION_GROUP), 1)
        possible_interceptors = [k for k in self.aircraft_inventory.keys() if k in INTERCEPT_CAPABLE]

        if len(possible_interceptors) <= 0:
            possible_interceptors = [k for k,v in self.aircraft_inventory.items() if k in CAP_CAPABLE and v >= 2]

        if number_of_interceptor_groups > 0:
            inventory = dict({k: v for k, v in self.aircraft_inventory.items() if k in possible_interceptors})
            for i in range(number_of_interceptor_groups):
                try:
                    unit = random.choice([k for k,v in inventory.items() if v >= 2])
                except IndexError:
                    break
                inventory[unit] = inventory[unit] - 2
                flight = Flight(unit, 2, self.from_cp, FlightType.INTERCEPTION)
                flight.points = []

                self.interceptor_flights.append(flight)
                self.flights.append(flight)

            # Update inventory
            for k, v in inventory.items():
                self.aircraft_inventory[k] = v

    def commision_cap(self):
        """
        Pick some aircraft to assign them to defensive CAP roles (BARCAP)
        """

        possible_aircraft = [k for k, v in self.aircraft_inventory.items() if k in CAP_CAPABLE and v >= 2]
        inventory = dict({k: v for k, v in self.aircraft_inventory.items() if k in possible_aircraft})

        offset = random.randint(0,5)
        for i in range(int(MISSION_DURATION/CAP_EVERY_X_MINUTES)):

            try:
                unit = random.choice([k for k, v in inventory.items() if v >= 2])
            except IndexError:
                break

            inventory[unit] = inventory[unit] - 2
            ftype = FlightType.BARCAP if self.from_cp.is_carrier else FlightType.CAP
            flight = Flight(unit, 2, self.from_cp, ftype)

            # Flight path : fly over each ground object (TODO : improve)
            flight.points = []
            flight.scheduled_in = offset + i*random.randint(CAP_EVERY_X_MINUTES-5, CAP_EVERY_X_MINUTES+5)

            patrol_alt = random.randint(PATROL_ALT_RANGE[0], PATROL_ALT_RANGE[1])

            patrolled = []
            for ground_object in self.from_cp.ground_objects:
                if ground_object.group_id not in patrolled and not ground_object.airbase_group:
                    point = FlightWaypoint(ground_object.position.x, ground_object.position.y, patrol_alt)
                    point.name = "Patrol point"
                    point.description = "Patrol #" + str(len(flight.points))
                    point.pretty_name = "Patrol #" + str(len(flight.points))
                    flight.points.append(point)
                    patrolled.append(ground_object.group_id)

            if len(flight.points) == 0:
                for i in range(3):
                    pos = self.from_cp.position.point_from_heading(random.randint(0, 360), random.randint(30000, 80000))
                    point = FlightWaypoint(pos.x, pos.y, patrol_alt)
                    point.name = "Patrol point"
                    point.description = "Patrol #" + str(len(flight.points))
                    point.pretty_name = "Patrol #" + str(len(flight.points))
                    flight.points.append(point)

            self.cap_flights.append(flight)
            self.flights.append(flight)

        # Update inventory
        for k, v in inventory.items():
            self.aircraft_inventory[k] = v

    def commision_cas(self):
        """
        Pick some aircraft to assign them to CAS
        """

        possible_aircraft = [k for k, v in self.aircraft_inventory.items() if k in CAS_CAPABLE and v >= 2]
        inventory = dict({k: v for k, v in self.aircraft_inventory.items() if k in possible_aircraft})
        cas_location = self._get_cas_locations()

        if len(cas_location) > 0:

            offset = random.randint(0,5)
            for i in range(int(MISSION_DURATION/CAS_EVERY_X_MINUTES)):

                try:
                    unit = random.choice([k for k, v in inventory.items() if v >= 2])
                except IndexError:
                    break

                inventory[unit] = inventory[unit] - 2
                flight = Flight(unit, 2, self.from_cp, FlightType.CAS)

                flight.points = []
                flight.scheduled_in = offset + i*random.randint(CAS_EVERY_X_MINUTES-5, CAS_EVERY_X_MINUTES+5)

                location = random.choice(cas_location)
                ingress, heading, distance = Conflict.frontline_vector(self.from_cp, location, self.game.theater)
                center = ingress.point_from_heading(heading, distance/2)
                egress = ingress.point_from_heading(heading, distance)

                flight.targets.append(center)

                ingress_point = FlightWaypoint(ingress.x, ingress.y, 1000)
                ingress_point.name = "INGRESS"
                ingress_point.pretty_name = "INGRESS"
                ingress_point.description = "Ingress into CAS area"
                flight.points.append(ingress_point)

                center_point = FlightWaypoint(center.x, center.y, 1000)
                center_point.description = "Provide CAS"
                center_point.name = "CAS"
                center_point.pretty_name = "INGRESS"
                flight.points.append(center_point)

                egress_point = FlightWaypoint(egress.x, egress.y, 1000)
                egress_point.description = "Egress from CAS area"
                egress_point.name = "EGRESS"
                egress_point.pretty_name = "EGRESS"
                flight.points.append(egress_point)

                self.cas_flights.append(flight)
                self.flights.append(flight)

            # Update inventory
            for k, v in inventory.items():
                self.aircraft_inventory[k] = v

    def commision_sead(self):
        """
        Pick some aircraft to assign them to SEAD tasks
        """

        possible_aircraft = [k for k, v in self.aircraft_inventory.items() if k in SEAD_CAPABLE and v >= 2]
        inventory = dict({k: v for k, v in self.aircraft_inventory.items() if k in possible_aircraft})

        if len(self.potential_sead_targets) > 0:

            offset = random.randint(0,5)
            for i in range(int(MISSION_DURATION/SEAD_EVERY_X_MINUTES)):

                if len(self.potential_sead_targets) <= 0:
                    break

                try:
                    unit = random.choice([k for k, v in inventory.items() if v >= 2])
                except IndexError:
                    break

                inventory[unit] = inventory[unit] - 2
                flight = Flight(unit, 2, self.from_cp, random.choice([FlightType.SEAD, FlightType.DEAD]))

                flight.points = []
                flight.scheduled_in = offset + i*random.randint(SEAD_EVERY_X_MINUTES-5, SEAD_EVERY_X_MINUTES+5)

                location = self.potential_sead_targets[0][0]
                self.potential_sead_targets.pop(0)

                heading = self.from_cp.position.heading_between_point(location.position)
                ingress_heading = heading - 180 + 25
                egress_heading = heading - 180 - 25

                ingress_pos = location.position.point_from_heading(ingress_heading, INGRESS_EGRESS_DISTANCE)
                ingress_point = FlightWaypoint(ingress_pos.x, ingress_pos.y, INGRESS_ALT)
                ingress_point.pretty_name = "INGRESS on " + location.obj_name
                ingress_point.description = "INGRESS on " + location.obj_name
                flight.points.append(ingress_point)

                point = FlightWaypoint(location.position.x, location.position.y, 1000)
                if flight.flight_type == FlightType.DEAD:
                    point.description = "SEAD on " + location.obj_name
                    point.pretty_name = "SEAD on " + location.obj_name
                else:
                    point.description = "DEAD on " + location.obj_name
                    point.pretty_name = "DEAD on " + location.obj_name

                point.targets.append(location)
                flight.points.append(point)

                egress_pos = location.position.point_from_heading(egress_heading, INGRESS_EGRESS_DISTANCE)
                egress_point = FlightWaypoint(egress_pos.x, egress_pos.y, EGRESS_ALT)
                egress_point.pretty_name = "EGRESS on " + location.obj_name
                egress_point.description = "EGRESS on " + location.obj_name
                flight.points.append(egress_point)

                self.sead_flights.append(flight)
                self.flights.append(flight)

            # Update inventory
            for k, v in inventory.items():
                self.aircraft_inventory[k] = v


    def commision_strike(self):
        """
        Pick some aircraft to assign them to STRIKE tasks
        """
        possible_aircraft = [k for k, v in self.aircraft_inventory.items() if k in CAS_CAPABLE and v >= 2]
        inventory = dict({k: v for k, v in self.aircraft_inventory.items() if k in possible_aircraft})

        if len(self.potential_strike_targets) > 0:

            offset = random.randint(0,5)
            for i in range(int(MISSION_DURATION/STRIKE_EVERY_X_MINUTES)):

                if len(self.potential_strike_targets) <= 0:
                    break

                try:
                    unit = random.choice([k for k, v in inventory.items() if v >= 2])
                except IndexError:
                    break

                inventory[unit] = inventory[unit] - 2
                flight = Flight(unit, 2, self.from_cp, FlightType.STRIKE)

                flight.points = []
                flight.scheduled_in = offset + i*random.randint(SEAD_EVERY_X_MINUTES-5, SEAD_EVERY_X_MINUTES+5)

                location = self.potential_strike_targets[0][0]
                self.potential_strike_targets.pop(0)

                heading = self.from_cp.position.heading_between_point(location.position)
                ingress_heading = heading - 180 + 25
                egress_heading = heading - 180 - 25

                ingress_pos = location.position.point_from_heading(ingress_heading, INGRESS_EGRESS_DISTANCE)
                ingress_point = FlightWaypoint(ingress_pos.x, ingress_pos.y, INGRESS_ALT)
                ingress_point.pretty_name = "INGRESS on " + location.obj_name
                ingress_point.description = "INGRESS on " + location.obj_name
                flight.points.append(ingress_point)

                if len(location.groups) > 0:
                    for g in location.groups:
                        for j, u in enumerate(g.units):
                            point = FlightWaypoint(u.position.x, u.position.y, 0)
                            point.description = "STRIKE " + "[" + str(location.obj_name) + "] : " + u.type + " #" + str(j)
                            point.pretty_name = "STRIKE " + "[" + str(location.obj_name) + "] : " + u.type + " #" + str(j)
                            ingress_point.targets.append(location)
                            flight.points.append(point)
                else:
                    point = FlightWaypoint(location.position.x, location.position.y, 0)
                    point.description = "STRIKE on " + location.obj_name + " " + str(location.category)
                    point.pretty_name = "STRIKE on " + location.obj_name + " " + str(location.category)
                    point.targets.append(location)
                    flight.points.append(point)

                egress_pos = location.position.point_from_heading(egress_heading, INGRESS_EGRESS_DISTANCE)
                egress_point = FlightWaypoint(egress_pos.x, egress_pos.y, EGRESS_ALT)
                egress_point.pretty_name = "EGRESS on " + location.obj_name
                egress_point.description = "EGRESS on " + location.obj_name
                flight.points.append(egress_point)

                self.strike_flights.append(flight)
                self.flights.append(flight)

            # Update inventory
            for k, v in inventory.items():
                self.aircraft_inventory[k] = v

    def _get_cas_locations(self):
        cas_locations = []
        for cp in self.from_cp.connected_points:
            if cp.captured != self.from_cp.captured:
                cas_locations.append(cp)
        return cas_locations

    def compute_strike_targets(self):
        """
        @return a list of potential strike targets in range
        """

        # target, distance
        self.potential_strike_targets = []

        for cp in [c for c in self.game.theater.controlpoints if c.captured != self.from_cp.captured]:

            # Compute distance to current cp
            distance = math.hypot(cp.position.x - self.from_cp.position.x,
                                  cp.position.y - self.from_cp.position.y)

            if distance > 2*STRIKE_MAX_RANGE:
                # Then it's unlikely any child ground object is in range
                return

            added_group = []
            for g in cp.ground_objects:
                if g.group_id in added_group or g.is_dead: continue

                # Compute distance to current cp
                distance = math.hypot(cp.position.x - self.from_cp.position.x,
                                      cp.position.y - self.from_cp.position.y)

                if distance < SEAD_MAX_RANGE:
                    self.potential_strike_targets.append((g, distance))
                    added_group.append(g)

        self.potential_strike_targets.sort(key=operator.itemgetter(1))

    def compute_sead_targets(self):
        """
        @return a list of potential sead targets in range
        """

        # target, distance
        self.potential_sead_targets = []

        for cp in [c for c in self.game.theater.controlpoints if c.captured != self.from_cp.captured]:

            # Compute distance to current cp
            distance = math.hypot(cp.position.x - self.from_cp.position.x,
                                  cp.position.y - self.from_cp.position.y)

            # Then it's unlikely any ground object is range
            if distance > 2*SEAD_MAX_RANGE:
                return

            for g in cp.ground_objects:

                if g.dcs_identifier == "AA":

                    # Check that there is at least one unit with a radar in the ground objects unit groups
                    number_of_units = sum([len([r for r in group.units if hasattr(db.unit_type_from_name(r.type), "detection_range")
                                                and db.unit_type_from_name(r.type).detection_range > 1000]) for group in g.groups])
                    if number_of_units <= 0:
                        continue

                    # Compute distance to current cp
                    distance = math.hypot(cp.position.x - self.from_cp.position.x,
                                          cp.position.y - self.from_cp.position.y)

                    if distance < SEAD_MAX_RANGE:
                        self.potential_sead_targets.append((g, distance))

        self.potential_sead_targets.sort(key=operator.itemgetter(1))

    def __repr__(self):
        return "-"*40 + "\n" + self.from_cp.name + " planned flights :\n"\
               + "-"*40 + "\n" + "\n".join([repr(f) for f in self.flights]) + "\n" + "-"*40

    def get_available_aircraft(self):
        base_aircraft_inventory = dict({k: v for k, v in self.from_cp.base.aircraft.items()})
        for f in self.flights:
            if f.unit_type in base_aircraft_inventory.keys():
                base_aircraft_inventory[f.unit_type] = base_aircraft_inventory[f.unit_type] - f.count
                if base_aircraft_inventory[f.unit_type] <= 0:
                    del base_aircraft_inventory[f.unit_type]
        return base_aircraft_inventory

