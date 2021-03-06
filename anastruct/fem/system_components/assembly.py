from anastruct.fem.elements import det_moment, det_shear
import numpy as np
import math


def set_force_vector(system, force_list):
    """
    :param force_list: list containing tuples with the
    1. number of the node,
    2. the number of the direction (1 = x, 2 = z, 3 = y)
    3. the force
    [(1, 3, 1000)] node=1, direction=3 (y), force=1000
    list may contain multiple tuples
    :return: Vector with forces on the nodes
    """
    for id_, direction, force in force_list:
        system.system_force_vector[(id_ - 1) * 3 + direction - 1] += force

    return system.system_force_vector


def prep_matrix_forces(system):
    system.system_force_vector = system.system_force_vector = np.zeros(
        len(system._vertices) * 3
    )
    apply_perpendicular_q_load(system)
    apply_point_load(system)
    apply_moment_load(system)


def apply_moment_load(system):
    for node_id, Ty in system.loads_moment.items():
        set_force_vector(system, [(node_id, 3, Ty * system.load_factor)])


def apply_point_load(system):
    for node_id in system.loads_point:
        Fx, Fz = system.loads_point[node_id]
        # system force vector.
        set_force_vector(
            system,
            [
                (node_id, 1, Fx * system.load_factor),
                (node_id, 2, Fz * system.load_factor),
            ],
        )


def apply_perpendicular_q_load(system):
    for element_id in system.loads_dead_load:
        element = system.element_map[element_id]
        q_perpendicular = element.all_q_load

        if q_perpendicular == 0:
            continue
        elif (
            element.q_direction == "x"
            or element.q_direction == "y"
            or element.dead_load
        ):
            apply_parallel_q_load(system, element)

        kl = element.constitutive_matrix[1][1] * 1e6
        kr = element.constitutive_matrix[2][2] * 1e6

        if math.isclose(kl, kr):
            left_moment = det_moment(kl, kr, q_perpendicular, 0, element.EI, element.l)
            right_moment = -left_moment
            rleft = det_shear(kl, kr, q_perpendicular, 0, element.EI, element.l)
            rright = rleft
        else:
            # minus because of systems positive rotation
            left_moment = det_moment(kl, kr, q_perpendicular, 0, element.EI, element.l)
            right_moment = -det_moment(
                kl, kr, q_perpendicular, element.l, element.EI, element.l
            )
            rleft = det_shear(kl, kr, q_perpendicular, 0, element.EI, element.l)
            rright = -det_shear(
                kl, kr, q_perpendicular, element.l, element.EI, element.l
            )

        rleft_x = rleft * math.sin(element.angle)
        rright_x = rright * math.sin(element.angle)

        rleft_z = rleft * math.cos(element.angle)
        rright_z = rright * math.cos(element.angle)

        if element.type == "truss":
            left_moment = 0
            right_moment = 0

        primary_force = np.array(
            [rleft_x, rleft_z, left_moment, rright_x, rright_z, right_moment]
        )
        element.element_primary_force_vector -= primary_force

        # Set force vector
        system.system_force_vector[
            (element.node_id1 - 1) * 3 : (element.node_id1 - 1) * 3 + 3
        ] += primary_force[0:3]
        system.system_force_vector[
            (element.node_id2 - 1) * 3 : (element.node_id2 - 1) * 3 + 3
        ] += primary_force[3:]


def apply_parallel_q_load(system, element):
    direction = element.q_direction
    # dead load
    factor_dl = abs(math.sin(element.angle))

    def update(Fx, Fz):
        element.element_primary_force_vector[0] -= Fx
        element.element_primary_force_vector[1] -= Fz
        element.element_primary_force_vector[3] -= Fx
        element.element_primary_force_vector[4] -= Fz

        set_force_vector(
            system,
            [
                (element.node_1.id, 2, Fz),
                (element.node_2.id, 2, Fz),
                (element.node_1.id, 1, Fx),
                (element.node_2.id, 1, Fx),
            ],
        )

    if direction == "x":
        factor = abs(math.cos(element.angle))

        for q_element in (element.q_load * factor, element.dead_load * factor_dl):
            # q_load working at parallel to the elements x-axis          # set the proper direction
            Fx = -q_element * math.cos(element.angle) * element.l * 0.5
            Fz = (
                q_element
                * abs(math.sin(element.angle))
                * element.l
                * 0.5
                * np.sign(math.sin(element.angle))
            )

            update(Fx, Fz)

    else:
        if math.isclose(element.angle, 0):
            # horizontal element cannot have parallel forces due to self weight or q-load in y direction.
            return None
        factor = abs(math.sin(element.angle))

        for q_element in (element.q_load * factor, element.dead_load * factor_dl):
            # q_load working at parallel to the elements x-axis          # set the proper direction
            Fx = (
                q_element
                * math.cos(element.angle)
                * element.l
                * 0.5
                * -np.sign(math.sin(element.angle))
            )
            Fz = q_element * abs(math.sin(element.angle)) * element.l * 0.5

            update(Fx, Fz)


def dead_load(system, g, element_id):
    system.loads_dead_load.add(element_id)
    system.element_map[element_id].dead_load = g


def assemble_system_matrix(system, validate=False, geometric_matrix=False):
    """
    Shape of the matrix = n nodes * n d.o.f.
    Shape = n * 3
    """
    system._remainder_indexes = []
    if not geometric_matrix:
        shape = len(system.node_map) * 3
        system.shape_system_matrix = shape
        system.system_matrix = np.zeros((shape, shape))

    for matrix_index, K in system.system_spring_map.items():
        #  first index is row, second is column
        system.system_matrix[matrix_index][matrix_index] += K

    # Determine the elements location in the stiffness matrix.
    # system matrix [K]
    #
    # [fx 1] [K         |  \ node 1 starts at row 1
    # |fz 1] |  K       |  /
    # |Ty 1] |   K      | /
    # |fx 2] |    K     |  \ node 2 starts at row 4
    # |fz 2] |     K    |  /
    # |Ty 2] |      K   | /
    # |fx 3] |       K  |  \ node 3 starts at row 7
    # |fz 3] |        K |  /
    # [Ty 3] [         K] /
    #
    #         n   n  n
    #         o   o  o
    #         d   d  d
    #         e   e  e
    #         1   2  3
    #
    # thus with appending numbers in the system matrix: column = row

    for i in range(len(system.element_map)):
        element = system.element_map[i + 1]
        element_matrix = element.stiffness_matrix

        # n1 and n2 are starting indexes of the rows and the columns for node 1 and node 2
        n1 = (element.node_1.id - 1) * 3
        n2 = (element.node_2.id - 1) * 3
        system.system_matrix[n1 : n1 + 3, n1 : n1 + 3] += element_matrix[0:3, :3]
        system.system_matrix[n1 : n1 + 3, n2 : n2 + 3] += element_matrix[0:3, 3:]

        system.system_matrix[n2 : n2 + 3, n1 : n1 + 3] += element_matrix[3:6, :3]
        system.system_matrix[n2 : n2 + 3, n2 : n2 + 3] += element_matrix[3:6, 3:]

    # returns True if symmetrical.
    if validate:
        assert np.allclose((system.system_matrix.transpose()), system.system_matrix)


def set_displacement_vector(system, nodes_list):
    """
    :param nodes_list: list containing tuples with
    1.the node
    2. the d.o.f. that is set
    :return: Vector with the displacements of the nodes (If displacement is not known, the value is set
    to NaN)
    """
    if system.system_displacement_vector is None:
        system.system_displacement_vector = np.ones(len(system._vertices) * 3) * np.NaN

    for i in nodes_list:
        index = (i[0] - 1) * 3 + i[1] - 1

        try:
            system.system_displacement_vector[index] = 0
        except IndexError as e:
            raise IndexError(
                e,
                "This often occurs if you set supports before the all the elements are modelled. "
                "First finish the model.",
            )
    return system.system_displacement_vector


def process_conditions(system):
    indexes = []
    # remove the unsolvable values from the matrix and vectors
    for i in range(system.shape_system_matrix):
        if system.system_displacement_vector[i] == 0:
            indexes.append(i)
        else:
            system._remainder_indexes.append(i)

    system.system_displacement_vector = np.delete(
        system.system_displacement_vector, indexes, 0
    )
    system.reduced_force_vector = np.delete(system.system_force_vector, indexes, 0)
    system.reduced_system_matrix = np.delete(system.system_matrix, indexes, 0)
    system.reduced_system_matrix = np.delete(system.reduced_system_matrix, indexes, 1)


def process_supports(system):
    for node in system.supports_hinged:
        set_displacement_vector(system, [(node.id, 1), (node.id, 2)])

    for i in range(len(system.supports_roll)):
        set_displacement_vector(
            system, [(system.supports_roll[i].id, system.supports_roll_direction[i])]
        )

    for node in system.supports_fixed:
        set_displacement_vector(system, [(node.id, 1), (node.id, 2), (node.id, 3)])

    for node, roll in system.supports_spring_x:
        if not roll:
            set_displacement_vector(system, [(node.id, 2)])

    for node, roll in system.supports_spring_z:
        if not roll:
            set_displacement_vector(system, [(node.id, 1)])

    for node, roll in system.supports_spring_y:
        if not roll:
            set_displacement_vector(system, [(node.id, 1), (node.id, 2)])

    for node_id, angle in system.inclined_roll.items():
        for el in system.node_element_map[node_id]:
            if el.node_1.id == node_id:
                el.a1 = el.angle + angle
            elif el.node_2.id == node_id:
                el.a2 = el.angle + angle

            el.compile_kinematic_matrix(el.a1, el.a2, el.l)
            el.compile_stiffness_matrix()
