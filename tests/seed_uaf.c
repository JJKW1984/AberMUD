
 /****************************************************************************\
 *									      *
 *			C R E A T O R    O F   L E G E N D S		      *
 *				(AberMud Version 5)			      *
 *									      *
 *  The Creator Of Legends System is (C) Copyright 1989 Alan Cox, All Rights  *
 *  Reserved.		  						      *
 *									      *
 \****************************************************************************/

/*
 *	Standalone test helper: seeds one real UFF record directly into a
 *	fresh UAF file, using the project's own SaveNewPersona() so the
 *	record's binary layout is guaranteed correct. Used by
 *	tests/smoke_test.py to set up a pre-existing persona for login/
 *	password scenarios that cannot be reached through the registration
 *	flow within the test harness (no loaded universe).
 */

#include "System.h"
#include <string.h>
#include <stdlib.h>

int main(int argc, char *argv[])
{
	UFF r;
	if(argc!=3)
	{
		fprintf(stderr,"usage: %s <name> <password>\n",argv[0]);
		return 1;
	}
	memset(&r,0,sizeof(r));
	strncpy(r.uff_Name,argv[1],31);
	strncpy(r.uff_Password,argv[2],8);
	r.uff_Level=1;
	r.uff_ActionTable=2;
	SaveNewPersona(&r);
	return 0;
}

void Log(char *x, ...){;}

void ErrFunc(char *x, char *y, char *z, int a, char *b)
{
	fprintf(stderr,"%s %s %s %d %s\n",x,y,z,a,b);
	exit(1);
}
