import numpy as np
import sys

M = np.loadtxt(sys.argv[1])

# Origin and spacing of reference space in mm
org_mm=np.array((-0.021092,-0.021092))
spc_mm=np.array((0.008035,0.008035))

# Matrix taking a pixel in x16 space int these physical coordinates
Q1=np.array(((-0.00804,-0.00000,0.02109),
            (-0.00000,-0.00804,0.02109),
            (0.00000,0.00000,1.00000)))

# Refspc matrix
Q2=np.array((( -0.00794,-0.00000,0.06603),
             (-0.00000,-0.00795,0.06602),
             (0,0,1)))

# Matrix taking pixel into x16 pixel space
A = np.dot(np.linalg.inv(Q2), np.dot(np.linalg.inv(M),Q1))
A = np.dot(np.linalg.inv(Q1), np.dot(M, Q2))

# Matrix taking this into full space
Q3 = np.array(((16.,0.,0.),(0.,16.,0.),(0.,0.,1.)))
AF = np.dot(Q3,A)

x=np.array((2500,2500,1))
print(np.dot(Q1,x))
print(np.dot(np.linalg.inv(M),np.dot(Q1,x)))
print(np.dot(np.linalg.inv(Q2),np.dot(np.linalg.inv(M),np.dot(Q1,x))))

# Print result
np.savetxt(sys.argv[2], AF)


